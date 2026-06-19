"""Working-copy management.

Per the project's safety model we never touch the source database. We snapshot it
into ``data/working/`` and read exclusively from that copy. Reads additionally use
SQLite's read-only URI mode as a second layer of protection.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from ..config import WORKING_DIR, ensure_dirs, find_source_db

WORKING_DB = WORKING_DIR / "schedulerdb.sqlite"


def refresh_working_copy(source: Path | None = None) -> Path | None:
    """Copy the source database into the working directory.

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
    """Return a path to a readable working copy, refreshing it by default."""
    if refresh or not WORKING_DB.is_file():
        return refresh_working_copy()
    return WORKING_DB if WORKING_DB.is_file() else None


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection in read-only mode with row access by name."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn
