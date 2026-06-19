"""Runtime configuration and path resolution for TS Assistant.

The app operates on a *copy* of the Target Scheduler SQLite database. The source
database is whatever the user drops into ``sample_database/`` (or an explicit path
given via the ``TS_ASSISTANT_DB`` environment variable). We never write to it.
"""

from __future__ import annotations

import os
from pathlib import Path

# backend/app/config.py -> repo root is three parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE_DB_DIR = REPO_ROOT / "sample_database"
DATA_DIR = REPO_ROOT / "data"
WORKING_DIR = DATA_DIR / "working"  # read-only snapshot for the reader
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
