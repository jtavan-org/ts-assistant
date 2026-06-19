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
WORKING_DIR = DATA_DIR / "working"
BACKUP_DIR = DATA_DIR / "backups"

# Candidate SQLite file extensions a Target Scheduler db might use.
_DB_GLOBS = ("*.sqlite", "*.sqlite3", "*.db")


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
    for d in (DATA_DIR, WORKING_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)
