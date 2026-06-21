"""TS Assistant FastAPI app.

Read-only companion API for NINA's Target Scheduler database (v1). Serves projects,
targets, and the HiPS survey catalog to the React/Aladin Lite frontend.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import (
    equipment,
    export,
    plan_templates,
    profiles,
    projects,
    surveys,
    templates,
)
from .config import BACKUP_DIR, find_source_db
from .db.backup import backup_signature

logger = logging.getLogger("ts_assistant")


def _db_error(source) -> str | None:
    """Open the DB the way reads/backups will, to surface perms/lock issues early."""
    if source is None:
        return None
    try:
        conn = sqlite3.connect(str(source))  # rw-capable, as reads/backups are
        try:
            conn.execute("PRAGMA user_version").fetchone()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — report any open failure, don't crash startup
        return f"cannot open the Target Scheduler database for read/write: {e}"
    return None


def _db_write_error(source) -> str | None:
    """Probe whether we could *save* — i.e. publish a file into the database's folder.

    Writes run on a local copy and are published back by writing a temp file in the
    database's directory and renaming it over the file (see :mod:`app.db.export`). So the
    meaningful check is whether that directory accepts a create + rename — NOT whether the
    source accepts in-place SQLite writes. The latter fails on a network share (CIFS/SMB)
    even though saves succeed via staging, so probing it would warn falsely. Non-
    destructive: never touches the database file itself.
    """
    if source is None:
        return None
    d = Path(source).parent
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".ts-write-probe-", dir=d)
        with os.fdopen(fd, "wb") as f:
            f.write(b"ok")
            f.flush()
            os.fsync(f.fileno())
        renamed = tmp + ".renamed"
        os.replace(tmp, renamed)  # exercise the publish rename, too
        tmp = renamed
    except OSError as e:
        return (
            "can't save changes — the folder containing the database isn't writable by "
            f"TS Assistant. Check the permissions on the mounted database directory. ({e})"
        )
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    source = find_source_db()
    if source is None:
        logger.warning(
            "TS Assistant: no Target Scheduler database found — set TS_ASSISTANT_DB or "
            "drop one into sample_database/."
        )
    else:
        logger.warning(
            "TS Assistant: operating in place on %s (a backup is taken before every write)",
            source,
        )
    yield


app = FastAPI(title="TS Assistant", version="0.1.0", lifespan=lifespan)

# The frontend may be served from any LAN host (the dev server binds 0.0.0.0),
# so allow any origin. This is a local, single-user tool with no cookies/auth.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix="/api")
app.include_router(surveys.router, prefix="/api")
app.include_router(equipment.router, prefix="/api")
app.include_router(export.router, prefix="/api")
app.include_router(templates.router, prefix="/api")
app.include_router(plan_templates.router, prefix="/api")
app.include_router(profiles.router, prefix="/api")


def _db_version(source) -> str | None:
    """Cheap change token for the source DB (size + mtime, incl. ``-wal``).

    Reuses :func:`backup_signature` so it stays consistent with the staged-write /
    publish logic — our own publish rewrites the source and bumps this, and an
    external NINA write does too (kfc). The frontend polls this token and triggers a
    state-preserving refresh when it changes. ``None`` when no DB is present or it
    can't be stat'd yet (degrade gracefully rather than 500).
    """
    if source is None:
        return None
    try:
        return backup_signature(source)
    except OSError:
        return None


@app.get("/api/health")
def health() -> dict:
    source = find_source_db()
    open_error = _db_error(source)
    # Only bother probing writability if the DB at least opens.
    write_error = _db_write_error(source) if (source and not open_error) else None
    return {
        "status": "ok",
        "db_present": source is not None,
        "db_path": str(source) if source else None,
        "backup_dir": str(BACKUP_DIR),
        "db_writable": source is not None and open_error is None and write_error is None,
        "write_error": write_error,
        "error": open_error,
        # Lightweight change token so the UI can detect external DB writes (kfc).
        "db_version": _db_version(source),
    }


@app.get("/api/db-version")
def db_version() -> dict:
    """Tiny, cheap change token the frontend polls to auto-refresh on DB change (kfc).

    Separate from /api/health (which also probes writability) so the poll stays
    near-free: a single ``stat`` of the source DB (+ its ``-wal`` sidecar).
    """
    source = find_source_db()
    return {"db_version": _db_version(source)}
