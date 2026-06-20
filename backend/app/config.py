"""Runtime configuration and path resolution for TS Assistant.

The app operates on a *copy* of the Target Scheduler SQLite database. The source
database is whatever the user drops into ``sample_database/`` (or an explicit path
given via the ``TS_ASSISTANT_DB`` environment variable). We never write to it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# backend/app/config.py -> repo root is three parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE_DB_DIR = REPO_ROOT / "sample_database"
DATA_DIR = REPO_ROOT / "data"
WORKING_DIR = DATA_DIR / "working"  # read-only snapshot for the reader
WORKING_DB = WORKING_DIR / "schedulerdb.sqlite"  # the snapshot the reader opens (staging)
BACKUP_DIR = DATA_DIR / "backups"  # timestamped pre-write backups (mh3.2)
EXPORT_DIR = DATA_DIR / "export"  # safe staging copy we write into by default
EXPORT_DB = EXPORT_DIR / "schedulerdb-export.sqlite"

# Candidate SQLite file extensions a Target Scheduler db might use.
_DB_GLOBS = ("*.sqlite", "*.sqlite3", "*.db")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


# Write-path knobs, read at call time so tests/operators can toggle via env.
def allow_live_write() -> bool:
    """Opt-in gate for writing to the *live* Target Scheduler DB (vs the staging copy)."""
    return _env_flag("TS_ASSISTANT_ALLOW_LIVE_WRITE")


def integrity_check_enabled() -> bool:
    """Run PRAGMA integrity_check before commit (slow on big DBs; on for live)."""
    return _env_flag("TS_ASSISTANT_INTEGRITY_CHECK")


def backup_gzip() -> bool:
    return _env_flag("TS_ASSISTANT_BACKUP_GZIP")


def backup_window_min() -> int:
    """Minutes within which our own back-to-back writes share one backup."""
    return _env_int("TS_ASSISTANT_BACKUP_WINDOW_MIN", 15)


def backup_keep_last() -> int:
    return _env_int("TS_ASSISTANT_BACKUP_KEEP_LAST", 10)


def backup_keep_days() -> int:
    return _env_int("TS_ASSISTANT_BACKUP_KEEP_DAYS", 14)


def find_source_db() -> Path | None:
    """Locate the source Target Scheduler database.

    Priority:
      1. ``TS_ASSISTANT_DB`` env var (explicit file path).
      2. First matching SQLite file in ``sample_database/``.

    Returns ``None`` when no database is available yet (e.g. before the user has
    copied one in) so the API can degrade gracefully instead of crashing.
    """
    env = os.environ.get("TS_ASSISTANT_DB")
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None

    if not SAMPLE_DB_DIR.is_dir():
        return None
    for pattern in _DB_GLOBS:
        matches = sorted(SAMPLE_DB_DIR.glob(pattern))
        if matches:
            return matches[0]
    return None


def ensure_dirs() -> None:
    for d in (DATA_DIR, WORKING_DIR, BACKUP_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --- operating mode (o2c / bead j9t) ---------------------------------------
#
# The app runs in one of two modes, fixed at launch (no in-app switch):
#
#   STAGING (default): reads a private snapshot of the source DB and writes to a
#     separate staging copy (data/export/). Safe, but created rows live only in
#     staging — you import the file into NINA yourself.
#   LIVE (TS_ASSISTANT_ALLOW_LIVE_WRITE=1): reads AND writes the user's *real*
#     Target Scheduler DB directly. No juggling; created rows persist on reload.
#     Only ever enabled deliberately at deploy time.
#
# ``build_mode`` is a PURE function of its two inputs so each seam (reader,
# writer, health) can feed it the bindings it owns (which tests monkeypatch) and
# still agree on the resulting mode — read and write can never desync.


@dataclass(frozen=True)
class ActiveMode:
    name: str  # "LIVE" | "STAGING"
    source: Path | None  # the user's real Target Scheduler DB (or None if absent)
    read_path: Path | None  # what the reader opens
    write_target: Path | None  # what writes land in
    integrity_check: bool  # verify DB integrity around writes
    backup_window_min: int | None  # None => use the configured default window
    live_error: str | None  # set when LIVE is requested but unusable


def build_mode(
    *,
    live: bool,
    source: Path | None,
    integrity_env: bool = False,
    staging_window: int | None = None,
) -> ActiveMode:
    """Map (live flag, source DB) -> the active operating mode. Pure; no I/O."""
    if live:
        if source is None:
            return ActiveMode(
                name="LIVE",
                source=None,
                read_path=None,
                write_target=None,
                integrity_check=True,
                backup_window_min=0,
                live_error=(
                    "Live mode is enabled (TS_ASSISTANT_ALLOW_LIVE_WRITE=1) but no "
                    "Target Scheduler database was found — set TS_ASSISTANT_DB or drop "
                    "one into sample_database/."
                ),
            )
        # Read target == write target == the real DB. Integrity on; back up every
        # write (window 0) so coalescing can never reuse a pre-NINA-change backup.
        return ActiveMode(
            name="LIVE",
            source=source,
            read_path=source,
            write_target=source,
            integrity_check=True,
            backup_window_min=0,
            live_error=None,
        )
    return ActiveMode(
        name="STAGING",
        source=source,
        read_path=WORKING_DB,
        write_target=EXPORT_DB,
        integrity_check=integrity_env,
        backup_window_min=staging_window,
        live_error=None,
    )


def active_mode() -> ActiveMode:
    """The current mode from this process's environment (config's own bindings)."""
    return build_mode(
        live=allow_live_write(),
        source=find_source_db(),
        integrity_env=integrity_check_enabled(),
        staging_window=backup_window_min(),
    )
