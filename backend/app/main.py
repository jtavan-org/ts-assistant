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
    return {
        "status": "ok",
        "db_present": source is not None,
        "db_path": str(source) if source else None,
        "backup_dir": str(BACKUP_DIR),
        "error": _db_error(source),
    }
