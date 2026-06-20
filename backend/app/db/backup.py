"""Consistent, time-coalesced database backups (first consumer of BACKUP_DIR).

Backups use SQLite's Online Backup API (``Connection.backup()``) so even a live DB
that NINA holds open snapshots without torn pages — a plain file copy can capture a
half-written page. Each backup lands in ``data/backups/`` with a tiny ``.json``
sidecar so coalescing/pruning never has to re-stat or reparse the big file.

Coalescing (the user's intent): before a write we only take a *new* backup if none
exists, the window has expired, or the DB changed since our last write (an external
edit). Our own back-to-back writes within the window share one restore point. The
post-write signature is recorded by the orchestrator (see export.py) so our own
commits don't masquerade as external changes.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

from ..config import (
    BACKUP_DIR,
    backup_gzip,
    backup_keep_days,
    backup_keep_last,
    backup_window_min,
    ensure_dirs,
)
from . import ops


class BackupInfo(BaseModel):
    path: str
    created_at: str  # ISO-8601 UTC
    source_path: str
    source_signature: str
    bytes: int
    gzip: bool = False


def backup_signature(db_path: Path) -> str:
    """Cheap change-detection token for a DB file: size + nanosecond mtime.

    Includes the ``-wal`` sidecar when present: a live DB NINA writes in WAL mode
    lands new data in ``-wal`` *without* changing the main file's size/mtime until a
    checkpoint, so a main-file-only signature would miss external writes and let
    coalescing reuse a backup taken before them. Folding ``-wal`` in closes that hole.
    """
    st = db_path.stat()
    sig = f"size:{st.st_size};mtime:{st.st_mtime_ns}"
    wal = db_path.with_name(db_path.name + "-wal")
    try:
        wst = wal.stat()
        sig += f";wal:{wst.st_size}:{wst.st_mtime_ns}"
    except FileNotFoundError:
        pass
    return sig


def consistent_copy(src: Path, dest: Path, *, live: bool = False) -> None:
    """Transactionally consistent copy of a SQLite DB via the Online Backup API.

    In ``live`` mode the source may be a WAL database NINA holds open; we open it
    read-write-capable (the backup API never modifies the source) so SQLite maps the
    ``-shm`` index and the copy captures all committed WAL frames. A ``mode=ro`` open
    can silently miss those frames — a backup that is "torn in time".
    """
    if live:
        src_conn = sqlite3.connect(str(src))
    else:
        src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dest_conn = sqlite3.connect(dest)
    try:
        with dest_conn:
            src_conn.backup(dest_conn)
    finally:
        src_conn.close()
        dest_conn.close()


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(1, 1000):
        cand = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not cand.exists():
            return cand
    raise RuntimeError("could not find a unique backup name")


def _sidecar(backup_path: Path) -> Path:
    return backup_path.with_name(backup_path.name + ".json")


def create_backup(
    db_path: Path,
    *,
    now: datetime | None = None,
    gzip_it: bool | None = None,
    live: bool = False,
) -> BackupInfo:
    ensure_dirs()
    now = now or datetime.now(timezone.utc)
    sig = backup_signature(db_path)
    sig8 = hashlib.sha1(sig.encode()).hexdigest()[:8]
    name = f"{db_path.stem}-{now.strftime('%Y%m%dT%H%M%SZ')}-{sig8}.sqlite"
    dest = _unique(BACKUP_DIR / name)
    consistent_copy(db_path, dest, live=live)

    do_gzip = backup_gzip() if gzip_it is None else gzip_it
    if do_gzip:
        gz = dest.with_name(dest.name + ".gz")
        with dest.open("rb") as f_in, gzip.open(gz, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        dest.unlink()
        dest = gz

    info = BackupInfo(
        path=str(dest),
        created_at=now.isoformat(),
        source_path=str(db_path.resolve()),
        source_signature=sig,
        bytes=dest.stat().st_size,
        gzip=do_gzip,
    )
    _sidecar(dest).write_text(info.model_dump_json(indent=2))
    return info


def _all_backups(db_path: Path) -> list[BackupInfo]:
    ensure_dirs()
    src = str(db_path.resolve())
    out: list[BackupInfo] = []
    for j in BACKUP_DIR.glob("*.json"):
        try:
            info = BackupInfo(**json.loads(j.read_text()))
        except Exception:
            continue
        if info.source_path == src:
            out.append(info)
    return out


def latest_backup(db_path: Path) -> BackupInfo | None:
    infos = _all_backups(db_path)
    return max(infos, key=lambda i: i.created_at) if infos else None


def should_backup(db_path: Path, *, window_min: int, now: datetime) -> bool:
    state = ops.get_target_state(str(db_path.resolve()))
    if latest_backup(db_path) is None or state.last_backup_time is None:
        return True
    if now - datetime.fromisoformat(state.last_backup_time) >= timedelta(minutes=window_min):
        return True  # window expired
    return backup_signature(db_path) != state.last_seen_signature  # external change


def ensure_backup(
    db_path: Path,
    *,
    window_min: int | None = None,
    now: datetime | None = None,
    live: bool = False,
) -> BackupInfo:
    """Return the backup that is this write's restore point — new or reused."""
    now = now or datetime.now(timezone.utc)
    window = backup_window_min() if window_min is None else window_min
    if should_backup(db_path, window_min=window, now=now):
        info = create_backup(db_path, now=now, live=live)
        ops.set_target_state(
            str(db_path.resolve()),
            last_backup_time=now.isoformat(),
            last_seen_signature=info.source_signature,
        )
        prune_backups(db_path, now=now)
        return info
    return latest_backup(db_path)  # type: ignore[return-value]  # non-None by should_backup


def prune_backups(
    db_path: Path,
    *,
    keep_last: int | None = None,
    keep_days: int | None = None,
    now: datetime | None = None,
) -> list[Path]:
    """Delete backups beyond BOTH retention bounds (last-K and last-D-days)."""
    keep_last = backup_keep_last() if keep_last is None else keep_last
    keep_days = backup_keep_days() if keep_days is None else keep_days
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=keep_days)
    infos = sorted(_all_backups(db_path), key=lambda i: i.created_at, reverse=True)
    removed: list[Path] = []
    for idx, info in enumerate(infos):
        within_last = idx < keep_last
        within_days = datetime.fromisoformat(info.created_at) >= cutoff
        if within_last or within_days:
            continue
        p = Path(info.path)
        p.unlink(missing_ok=True)
        _sidecar(p).unlink(missing_ok=True)
        removed.append(p)
    return removed
