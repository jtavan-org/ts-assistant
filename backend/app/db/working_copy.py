"""Read seam — opens the one Target Scheduler database in place.

The app reads (and writes) the user's real database directly; there is no snapshot
copy. The read connection is opened read-write-capable but **read-only by
discipline** (we only ever issue SELECTs through it). That matters because a
``mode=ro`` connection on a DB NINA holds open in WAL journal mode can silently miss
committed-but-uncheckpointed frames; an rw-capable handle maps the ``-shm`` index and
sees all committed data. Writes get their own transactional, backed-up path
(:mod:`app.db.export`).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config import find_source_db

# Fail fast rather than hang a read request if the DB is momentarily locked.
READ_TIMEOUT = 5.0


def get_working_copy(refresh: bool = True) -> Path | None:
    """Return the database the reader should open (the source DB itself), or None.

    ``refresh`` is accepted for call-site compatibility but is a no-op now that reads
    go directly to the source (no snapshot to refresh).
    """
    return find_source_db()  # may be None -> caller returns empty


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a read connection (rows by name), rw-capable so WAL frames are visible."""
    conn = sqlite3.connect(str(db_path), timeout=READ_TIMEOUT)
    conn.row_factory = sqlite3.Row
    return conn
