"""Faithful Target Scheduler writer (seed for the P3 write/export path).

Writes a Project + its Targets + their ExposurePlans (and the ExposureTemplates
the plans reference) into a Target Scheduler SQLite database whose schema is
byte-identical to NINA's (see :mod:`app.db.schema`). The intent is fidelity:

* RA is persisted in **hours** (``ra_hours = ra_deg / 15``) and Dec in degrees,
  matching how the EF ``Target`` entity stores them — the exact inverse of what
  :mod:`app.db.reader` does on the way out.
* Each row gets a ``guid`` (uuid4 text) like NINA's rows.
* ``Id`` columns are rowid aliases, so a plain INSERT lets SQLite assign the next
  Id; ``cursor.lastrowid`` is the assigned value.

This module is deliberately side-effect-light (it just runs INSERTs on a caller-
provided connection); the transactional/backup wrapper is mh3.2.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from .schema import create_schema


class ExposurePlanSpec(BaseModel):
    """One exposure plan, identified by the filter it images through."""

    filter_name: str
    exposure: float
    desired: int = 1
    acquired: int = 0
    accepted: int = 0


class TargetSpec(BaseModel):
    """A single pointing. Coordinates are in DEGREES (converted to hours on write)."""

    name: str
    ra_deg: float
    dec_deg: float
    rotation: float = 0.0
    roi: float = 100.0
    active: bool = True
    epoch_code: int = 0  # 0 = J2000
    exposure_plans: list[ExposurePlanSpec] = Field(default_factory=list)


class ProjectSpec(BaseModel):
    """A project with its targets — the unit the writer/exporter persists."""

    profile_id: str
    name: str
    description: str | None = None
    state: int = 0  # 0 = draft
    priority: int = 1
    is_mosaic: bool = False
    targets: list[TargetSpec] = Field(default_factory=list)


class WriteResult(BaseModel):
    project_id: int
    target_ids: list[int]
    plan_ids: list[int]
    template_ids: dict[str, int]  # filter name -> exposuretemplate.Id


# The columns each INSERT below populates — the writer's column contract, used by
# the validator (mh3.3) to check the target schema is compatible. Keep in sync
# with the INSERT statements in write_project/_ensure_template.
WRITTEN_COLUMNS: dict[str, frozenset[str]] = {
    "project": frozenset(
        {"profileId", "name", "description", "state", "priority", "isMosaic", "guid"}
    ),
    "target": frozenset(
        {"name", "active", "ra", "dec", "epochcode", "rotation", "roi", "projectid", "guid"}
    ),
    "exposuretemplate": frozenset(
        {"profileId", "name", "filtername", "defaultexposure", "guid"}
    ),
    "exposureplan": frozenset(
        {
            "profileId", "exposure", "desired", "acquired", "accepted",
            "targetid", "exposureTemplateId", "enabled", "guid",
        }
    ),
}


def _guid() -> str:
    return str(uuid.uuid4())


def create_scheduler_db(path: str | Path) -> sqlite3.Connection:
    """Create a fresh database with the full canonical Target Scheduler schema."""
    path = Path(path)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    create_schema(conn)
    conn.commit()
    return conn


def _ensure_template(
    conn: sqlite3.Connection, profile_id: str, filter_name: str, cache: dict[str, int]
) -> int:
    """Return the exposuretemplate.Id for a (profile, filter), creating it once."""
    if filter_name in cache:
        return cache[filter_name]
    cur = conn.execute(
        "INSERT INTO exposuretemplate (profileId, name, filtername, defaultexposure, guid)"
        " VALUES (?, ?, ?, ?, ?)",
        (profile_id, filter_name, filter_name, 300.0, _guid()),
    )
    cache[filter_name] = int(cur.lastrowid)
    return cache[filter_name]


def write_project(conn: sqlite3.Connection, spec: ProjectSpec) -> WriteResult:
    """Insert a project, its targets, and their exposure plans. Does not commit."""
    pcur = conn.execute(
        "INSERT INTO project (profileId, name, description, state, priority, isMosaic, guid)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            spec.profile_id,
            spec.name,
            spec.description,
            spec.state,
            spec.priority,
            1 if spec.is_mosaic else 0,
            _guid(),
        ),
    )
    project_id = int(pcur.lastrowid)

    templates: dict[str, int] = {}
    target_ids: list[int] = []
    plan_ids: list[int] = []

    for t in spec.targets:
        tcur = conn.execute(
            "INSERT INTO target (name, active, ra, dec, epochcode, rotation, roi, projectid, guid)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                t.name,
                1 if t.active else 0,
                t.ra_deg / 15.0,  # persist RA in HOURS
                t.dec_deg,
                t.epoch_code,
                t.rotation,
                t.roi,
                project_id,
                _guid(),
            ),
        )
        target_id = int(tcur.lastrowid)
        target_ids.append(target_id)

        for p in t.exposure_plans:
            template_id = _ensure_template(conn, spec.profile_id, p.filter_name, templates)
            ecur = conn.execute(
                "INSERT INTO exposureplan"
                " (profileId, exposure, desired, acquired, accepted, targetid,"
                "  exposureTemplateId, enabled, guid)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    spec.profile_id,
                    p.exposure,
                    p.desired,
                    p.acquired,
                    p.accepted,
                    target_id,
                    template_id,
                    _guid(),
                ),
            )
            plan_ids.append(int(ecur.lastrowid))

    return WriteResult(
        project_id=project_id,
        target_ids=target_ids,
        plan_ids=plan_ids,
        template_ids=templates,
    )
