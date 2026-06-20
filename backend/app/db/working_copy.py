"""Working-copy management — the single read seam, mode-aware (o2c / bead j9t).

STAGING mode (default): we never touch the source database. We snapshot it into
``data/working/`` and read exclusively from that private copy, additionally using
SQLite's read-only URI mode as a second layer of protection.

LIVE mode (``TS_ASSISTANT_ALLOW_LIVE_WRITE=1``): the reader opens the user's *real*
Target Scheduler DB directly — no snapshot copy (so just-written rows show up on the
next read) — and opens it read-write-capable but **read-only by discipline** (we only
ever issue SELECTs here). That matters because a ``mode=ro`` connection on a DB NINA
holds open in WAL journal mode can silently miss committed-but-uncheckpointed frames;
an rw-capable handle maps the ``-shm`` index and sees all committed data.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from ..config import (
    WORKING_DB,
    allow_live_write,
    build_mode,
    ensure_dirs,
    find_source_db,
)

# Fail fast rather than hang a read request if the live DB is momentarily locked.
READ_TIMEOUT = 5.0


def _mode():
    return build_mode(live=allow_live_write(), source=find_source_db())


def refresh_working_copy(source: Path | None = None) -> Path | None:
    """Copy the source database into the working directory (STAGING only).

    Returns the working-copy path, or ``None`` if no source database exists yet.
    """
    ensure_dirs()
    src = source or find_source_db()
    if src is None or not src.is_file():
        return None
    # copy2 preserves mtime of the *copy*; the source is only read.
    shutil.copy2(src, WORKING_DB)
    return WORKING_DB


def get_working_copy(refresh: bool = True) -> Path | None:
    """Return the path the reader should open, resolved by the active mode.

    LIVE: the real source DB directly (no copy). STAGING: a refreshed snapshot.
    """
    if _mode().name == "LIVE":
        return find_source_db()  # may be None -> caller returns empty
    if refresh or not WORKING_DB.is_file():
        return refresh_working_copy()
    return WORKING_DB if WORKING_DB.is_file() else None


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a read connection appropriate to the active mode (row access by name).

    LIVE: rw-capable (WAL-visible), read-only by discipline. STAGING: ``mode=ro`` on
    the private snapshot, which has no concurrent writer.
    """
    if _mode().name == "LIVE":
        conn = sqlite3.connect(str(db_path), timeout=READ_TIMEOUT)
    else:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn
