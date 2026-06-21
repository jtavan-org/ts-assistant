"""TS Assistant FastAPI app.

Read-only companion API for NINA's Target Scheduler database (v1). Serves projects,
targets, and the HiPS survey catalog to the React/Aladin Lite frontend.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager

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

logger = logging.getLogger("ts_assistant")


# Keep health responsive even if the DB is momentarily locked.
_HEALTH_TIMEOUT = 2.0


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
    """Probe that the database is actually *writable*, reverting immediately.

    A read — or even ``BEGIN IMMEDIATE`` — can succeed on a database whose real page
    writes still fail (notably SQLite over a network share / file-sync replica such as
    SMB/CIFS/NFS, where the journal/page write is where it breaks). So we exercise the
    actual write path inside a transaction and roll it back: nothing is persisted.
    """
    if source is None:
        return None
    try:
        conn = sqlite3.connect(str(source), timeout=_HEALTH_TIMEOUT)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE IF NOT EXISTS _ts_write_probe (x)")
            conn.execute("ROLLBACK")  # revert — the probe table never persists
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — report, don't crash
        msg = str(e).lower()
        if "readonly" in msg or "read-only" in msg or "read only" in msg:
            return (
                "database is read-only — it is most likely on a network share or "
                "file-sync replica (SMB/CIFS/NFS, Syncthing/Dropbox, etc.) that does not "
                "reliably accept SQLite writes, or the file is write-protected. Point TS "
                f"Assistant at a database on writable local storage. ({e})"
            )
        if "lock" in msg or "busy" in msg:
            return f"database is busy (open by NINA?) — could not verify writability: {e}"
        return f"database is not writable: {e}"
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
    }
