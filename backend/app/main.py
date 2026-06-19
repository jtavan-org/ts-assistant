"""TS Assistant FastAPI app.

Read-only companion API for NINA's Target Scheduler database (v1). Serves projects,
targets, and the HiPS survey catalog to the React/Aladin Lite frontend.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import equipment, export, projects, surveys
from .config import find_source_db

app = FastAPI(title="TS Assistant", version="0.1.0")

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


@app.get("/api/health")
def health() -> dict:
    src = find_source_db()
    return {
        "status": "ok",
        "db_present": src is not None,
        "source_db": str(src) if src else None,
    }
