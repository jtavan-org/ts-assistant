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

from .api import equipment, export, plan_templates, projects, surveys, templates
from .config import ActiveMode, active_mode

logger = logging.getLogger("ts_assistant")

# Set once at startup: a reason live mode is unusable beyond "no DB found" (e.g. the
# live DB can't be opened for read/write). Surfaced via /api/health so the UI can warn.
_LIVE_PROBE_ERROR: str | None = None


def _probe_live(mode: ActiveMode) -> str | None:
    """Open the live DB the way reads/backups will, to surface perms/lock issues early."""
    if mode.name != "LIVE" or mode.source is None:
        return None
    try:
        conn = sqlite3.connect(str(mode.source))  # rw-capable, as live reads/backups are
        try:
            conn.execute("PRAGMA user_version").fetchone()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — report any open failure, don't crash startup
        return f"cannot open the live Target Scheduler database for read/write: {e}"
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _LIVE_PROBE_ERROR
    mode = active_mode()
    _LIVE_PROBE_ERROR = mode.live_error or _probe_live(mode)
    if mode.name == "LIVE" and _LIVE_PROBE_ERROR:
        logger.warning("TS Assistant: LIVE mode requested but MISCONFIGURED — %s", _LIVE_PROBE_ERROR)
    elif mode.name == "LIVE":
        logger.warning(
            "TS Assistant: LIVE/PRODUCTION mode — reads AND writes the real DB at %s",
            mode.write_target,
        )
    else:
        logger.info("TS Assistant: STAGING mode — writes go to %s", mode.write_target)
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


@app.get("/api/health")
def health() -> dict:
    mode = active_mode()
    return {
        "status": "ok",
        "db_present": mode.source is not None,
        "source_db": str(mode.source) if mode.source else None,
        "mode": mode.name,  # "LIVE" | "STAGING"
        "read_path": str(mode.read_path) if mode.read_path else None,
        "write_target": str(mode.write_target) if mode.write_target else None,
        "live_error": mode.live_error or _LIVE_PROBE_ERROR,
    }
