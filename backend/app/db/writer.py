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
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from .schema import create_schema


class ExposurePlanSpec(BaseModel):
    """One exposure plan.

    Either references an existing exposure *template* by Id
    (``exposure_template_id``) — the qiz.1 picker path, where the plan inherits the
    user's real gain/offset/bin/moon-avoidance settings — or, when no template is
    chosen, falls back to ``filter_name`` and a bare template is auto-created
    per (profile, filter) for compatibility with the original Save flow.
    """

    filter_name: str | None = None
    exposure: float
    desired: int = 1
    acquired: int = 0
    accepted: int = 0
    exposure_template_id: int | None = None


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


class ExposureTemplateSpec(BaseModel):
    """A full exposure template (capture definition) to create (qiz.5).

    Unlike the bare template the project path auto-creates (:func:`_ensure_template`),
    this carries all of NINA's tunables. Unset/advanced fields default to NINA's own
    defaults so a minimal "name + filter" create still produces a valid row.
    """

    profile_id: str
    name: str
    filter_name: str
    # -1 is NINA's "use the default" sentinel (camera default for gain/offset/readout,
    # project default for dither) — matches what Target Scheduler itself stores.
    gain: int | None = -1
    offset: int | None = -1
    binning: int | None = 1
    readout_mode: int | None = -1
    twilight_level: int = 0
    moon_avoidance_enabled: bool = False
    moon_avoidance_separation: float = 0.0
    moon_avoidance_width: int = 0
    maximum_humidity: float | None = None
    default_exposure: float = 60.0
    moon_relax_scale: float = 0.0
    moon_relax_max_altitude: float = 5.0
    moon_relax_min_altitude: float = -15.0
    moon_down_enabled: bool = False
    dither_every: int = -1
    minutes_offset: int = 0


class WriteResult(BaseModel):
    project_id: int
    target_ids: list[int]
    plan_ids: list[int]
    template_ids: dict[str, int]  # filter name -> exposuretemplate.Id
    ruleweight_ids: list[int] = Field(default_factory=list)


# NINA creates one ruleweight row per scoring rule for EVERY project (these 8 rules
# with these default weights — identical across all real projects). A project with NO
# ruleweights crashes Target Scheduler's scheduler when it scores it, so the writer
# seeds them exactly like NINA does (bead nil). (name, weight) pairs:
DEFAULT_RULE_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("Meridian Flip Penalty", 0.0),
    ("Meridian Window Priority", 75.0),
    ("Mosaic Completion", 0.0),
    ("Percent Complete", 50.0),
    ("Project Priority", 50.0),
    ("Setting Soonest", 50.0),
    ("Smart Exposure Order", 0.0),
    ("Target Switch Penalty", 67.0),
)


# The columns each INSERT below populates — the writer's column contract, used by
# the validator (mh3.3) to check the target schema is compatible. Keep in sync
# with the INSERT statements in write_project/_ensure_template.
WRITTEN_COLUMNS: dict[str, frozenset[str]] = {
    "project": frozenset(
        {
            "profileId", "name", "description", "state", "priority", "isMosaic",
            # NINA's Target Scheduler EF model maps these to NON-nullable .NET types,
            # so leaving them NULL makes EF throw while materializing the project list
            # → every project vanishes in NINA (bead nil). They're nullable in SQLite,
            # so we must populate them ourselves with NINA's Project defaults.
            "createdate", "minimumtime", "minimumaltitude", "usecustomhorizon",
            "horizonoffset", "meridianwindow", "filterswitchfrequency", "ditherevery",
            "enablegrader", "guid",
        }
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
    # ruleweight has no guid column; one row per scoring rule per project.
    "ruleweight": frozenset({"name", "weight", "projectid"}),
}

# Full column set written by write_exposure_template (qiz.5). Kept separate from the
# bare project-path contract above so the project path's column contract is unchanged.
TEMPLATE_WRITTEN_COLUMNS: frozenset[str] = frozenset(
    {
        "profileId", "name", "filtername", "gain", "offset", "bin", "readoutmode",
        "twilightlevel", "moonavoidanceenabled", "moonavoidanceseparation",
        "moonavoidancewidth", "maximumhumidity", "defaultexposure", "moonrelaxscale",
        "moonrelaxmaxaltitude", "moonrelaxminaltitude", "moondownenabled", "ditherevery",
        "minutesOffset", "guid",
    }
)


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
    # NINA fills these project columns at the app layer (they're nullable in SQLite
    # but non-nullable in its EF model). We must write them too, or NINA's projects
    # query throws on the NULLs and shows no projects at all (bead nil). Values are
    # NINA's own Project defaults; createdate is stored as Unix seconds like NINA.
    pcur = conn.execute(
        "INSERT INTO project"
        " (profileId, name, description, state, priority, isMosaic,"
        "  createdate, minimumtime, minimumaltitude, usecustomhorizon, horizonoffset,"
        "  meridianwindow, filterswitchfrequency, ditherevery, enablegrader, guid)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            spec.profile_id,
            spec.name,
            spec.description,
            spec.state,
            spec.priority,
            1 if spec.is_mosaic else 0,
            int(time.time()),  # createdate (Unix seconds)
            30,                # minimumtime (minutes)
            0.0,               # minimumaltitude
            0,                 # usecustomhorizon (false)
            0.0,               # horizonoffset
            0,                 # meridianwindow
            0,                 # filterswitchfrequency
            0,                 # ditherevery (0 = off)
            1,                 # enablegrader (true)
            _guid(),
        ),
    )
    project_id = int(pcur.lastrowid)

    # Seed NINA's default scoring rules; a project without them crashes Target Scheduler.
    ruleweight_ids: list[int] = []
    for rname, rweight in DEFAULT_RULE_WEIGHTS:
        rcur = conn.execute(
            "INSERT INTO ruleweight (name, weight, projectid) VALUES (?, ?, ?)",
            (rname, rweight, project_id),
        )
        ruleweight_ids.append(int(rcur.lastrowid))

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
            if p.exposure_template_id is not None:
                # Reference an existing template (qiz.1 picker): inherits the user's
                # real settings; not ours, so never recorded in template_ids/provenance.
                template_id = p.exposure_template_id
            elif p.filter_name:
                template_id = _ensure_template(conn, spec.profile_id, p.filter_name, templates)
            else:
                raise ValueError(
                    "exposure plan needs either exposure_template_id or filter_name"
                )
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
        ruleweight_ids=ruleweight_ids,
    )


def write_exposure_template(conn: sqlite3.Connection, spec: ExposureTemplateSpec) -> int:
    """Insert one full exposure template; return its new Id. Does not commit."""
    cur = conn.execute(
        "INSERT INTO exposuretemplate"
        " (profileId, name, filtername, gain, offset, bin, readoutmode, twilightlevel,"
        "  moonavoidanceenabled, moonavoidanceseparation, moonavoidancewidth, maximumhumidity,"
        "  defaultexposure, moonrelaxscale, moonrelaxmaxaltitude, moonrelaxminaltitude,"
        "  moondownenabled, ditherevery, minutesOffset, guid)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            spec.profile_id,
            spec.name,
            spec.filter_name,
            spec.gain,
            spec.offset,
            spec.binning,
            spec.readout_mode,
            spec.twilight_level,
            1 if spec.moon_avoidance_enabled else 0,
            spec.moon_avoidance_separation,
            spec.moon_avoidance_width,
            spec.maximum_humidity,
            spec.default_exposure,
            spec.moon_relax_scale,
            spec.moon_relax_max_altitude,
            spec.moon_relax_min_altitude,
            1 if spec.moon_down_enabled else 0,
            spec.dither_every,
            spec.minutes_offset,
            _guid(),
        ),
    )
    return int(cur.lastrowid)
