"""Runtime configuration and path resolution for TS Assistant.

The app operates **in place** on a single Target Scheduler SQLite database — the one
the user drops into ``sample_database/`` (or an explicit path via the
``TS_ASSISTANT_DB`` environment variable). Reads and writes target that same file; a
consistent backup is taken into ``data/backups/`` before every write (see
:mod:`app.db.backup`), which is the safety mechanism.
"""

from __future__ import annotations

import os
from pathlib import Path

# backend/app/config.py -> repo root is three parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE_DB_DIR = REPO_ROOT / "sample_database"
DATA_DIR = REPO_ROOT / "data"
BACKUP_DIR = DATA_DIR / "backups"  # timestamped pre-write backups

# Candidate SQLite file extensions a Target Scheduler db might use.
_DB_GLOBS = ("*.sqlite", "*.sqlite3", "*.db")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


# Backup knobs, read at call time so tests/operators can toggle via env.
def backup_gzip() -> bool:
    return _env_flag("TS_ASSISTANT_BACKUP_GZIP")


def backup_keep_last() -> int:
    return _env_int("TS_ASSISTANT_BACKUP_KEEP_LAST", 10)


def backup_keep_days() -> int:
    return _env_int("TS_ASSISTANT_BACKUP_KEEP_DAYS", 14)


# Canonical NINA Target Scheduler database filenames, tried before any glob.
_CANONICAL_DB_NAMES = ("schedulerdb.sqlite", "schedulerdb.sqlite3", "schedulerdb.db")


def find_source_db() -> Path | None:
    """Locate the source Target Scheduler database.

    Priority:
      1. ``TS_ASSISTANT_DB`` env var (explicit file path).
      2. The canonical ``schedulerdb.sqlite`` (or .sqlite3/.db) in ``sample_database/``.
      3. First non-backup SQLite file in ``sample_database/``.

    A real Target Scheduler folder holds ``schedulerdb.sqlite`` alongside NINA's own
    dated backups (e.g. ``schedulerdb-2026-06-16-21-23-06-backup.sqlite``). A naive
    ``sorted(glob(...))[0]`` would pick a backup, since ``-`` sorts before ``.`` — so we
    prefer the canonical name and skip anything that looks like a backup.

    Returns ``None`` when no database is available yet (e.g. before the user has
    copied one in) so the API can degrade gracefully instead of crashing.
    """
    env = os.environ.get("TS_ASSISTANT_DB")
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None

    if not SAMPLE_DB_DIR.is_dir():
        return None

    for name in _CANONICAL_DB_NAMES:
        p = SAMPLE_DB_DIR / name
        if p.is_file():
            return p

    for pattern in _DB_GLOBS:
        matches = sorted(
            m for m in SAMPLE_DB_DIR.glob(pattern) if "backup" not in m.name.lower()
        )
        if matches:
            return matches[0]
    return None


def ensure_dirs() -> None:
    for d in (DATA_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)
