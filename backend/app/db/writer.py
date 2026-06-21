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
    # Target Scheduler's per-plan enable flag (default enabled); a disabled plan is
    # skipped by the scheduler. Carried through create/update so a toggle persists.
    enabled: bool = True


class TargetSpec(BaseModel):
    """A single pointing. Coordinates are in DEGREES (converted to hours on write)."""

    # The existing DB target Id when editing (o2c); None = a new target to insert.
    id: int | None = None
    name: str
    ra_deg: float
    dec_deg: float
    rotation: float = 0.0
    roi: float = 100.0
    active: bool = True
    epoch_code: int = 0  # 0 = J2000
    exposure_plans: list[ExposurePlanSpec] = Field(default_factory=list)


class RuleWeightSpec(BaseModel):
    """One scoring-rule weight override (qiz.3). ``name`` must match a NINA rule."""

    name: str
    weight: float


class OverrideStepSpec(BaseModel):
    """One step in a target's override exposure order (awh).

    ``action`` 0 = take the exposure plan at ``reference_idx`` (0-based index into the
    target's plans); ``action`` 1 = a Dither step (``reference_idx`` = -1).
    """

    action: int = 0
    reference_idx: int = -1


class ProjectSpec(BaseModel):
    """A project with its targets — the unit the writer/exporter persists."""

    profile_id: str
    name: str
    description: str | None = None
    state: int = 0  # 0 = draft
    priority: int = 1
    is_mosaic: bool = False
    targets: list[TargetSpec] = Field(default_factory=list)
    # Optional per-rule weight overrides (qiz.3). Unset → NINA defaults; provided
    # weights override matching rules by name, others keep their default.
    rule_weights: list[RuleWeightSpec] | None = None
    # Advanced project settings (psq) — NINA's project-tab knobs. Defaults match NINA.
    minimum_time: int = 30
    minimum_altitude: float = 0.0
    maximum_altitude: float = 0.0
    use_custom_horizon: bool = False
    horizon_offset: float = 0.0
    meridian_window: int = 0
    filter_switch_frequency: int = 0
    dither_every: int = 0
    enable_grader: bool = True
    flats_handling: int = 0
    smart_exposure_order: bool = False
    # Override exposure order (awh) — an explicit per-project step list applied to every
    # target. None/empty → no override (NINA uses its default cadence).
    override_exposure_order: list[OverrideStepSpec] | None = None


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
            # psq advanced settings (present in the canonical schema as ALTER-added cols).
            "maximumAltitude", "flatsHandling", "smartexposureorder",
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
    # override exposure order (awh) — ordered per-target steps; no guid column.
    "overrideexposureorderitem": frozenset({"targetid", "order", "action", "referenceIdx"}),
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
        "  meridianwindow, filterswitchfrequency, ditherevery, enablegrader,"
        "  maximumAltitude, flatsHandling, smartexposureorder, guid)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            spec.profile_id,
            spec.name,
            spec.description,
            spec.state,
            spec.priority,
            1 if spec.is_mosaic else 0,
            int(time.time()),  # createdate (Unix seconds)
            spec.minimum_time,
            spec.minimum_altitude,
            1 if spec.use_custom_horizon else 0,
            spec.horizon_offset,
            spec.meridian_window,
            spec.filter_switch_frequency,
            spec.dither_every,
            1 if spec.enable_grader else 0,
            spec.maximum_altitude,
            spec.flats_handling,
            1 if spec.smart_exposure_order else 0,
            _guid(),
        ),
    )
    project_id = int(pcur.lastrowid)

    # Seed NINA's scoring rules; a project without all of them crashes Target
    # Scheduler. Always write the full set of 8, applying any user weight overrides
    # by name (qiz.3) so the crash-safety invariant holds regardless of input.
    overrides = {rw.name: rw.weight for rw in (spec.rule_weights or [])}
    ruleweight_ids: list[int] = []
    for rname, rweight in DEFAULT_RULE_WEIGHTS:
        rcur = conn.execute(
            "INSERT INTO ruleweight (name, weight, projectid) VALUES (?, ?, ?)",
            (rname, overrides.get(rname, rweight), project_id),
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
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    spec.profile_id,
                    p.exposure,
                    p.desired,
                    p.acquired,
                    p.accepted,
                    target_id,
                    template_id,
                    1 if p.enabled else 0,
                    _guid(),
                ),
            )
            plan_ids.append(int(ecur.lastrowid))

        _insert_override_order(conn, target_id, spec.override_exposure_order)

    return WriteResult(
        project_id=project_id,
        target_ids=target_ids,
        plan_ids=plan_ids,
        template_ids=templates,
        ruleweight_ids=ruleweight_ids,
    )


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND lower(name)=lower(?)",
            (name,),
        ).fetchone()
        is not None
    )


def _insert_override_order(
    conn: sqlite3.Connection, target_id: int, steps: list[OverrideStepSpec] | None
) -> None:
    """Write a target's override exposure order rows (awh). No-op when empty."""
    for i, s in enumerate(steps or [], start=1):
        conn.execute(
            'INSERT INTO overrideexposureorderitem (targetid, "order", action, referenceIdx)'
            " VALUES (?, ?, ?, ?)",
            (target_id, i, s.action, s.reference_idx),
        )


def _insert_plan(
    conn: sqlite3.Connection,
    profile_id: str,
    target_id: int,
    p: ExposurePlanSpec,
    templates: dict[str, int],
    plan_ids: list[int],
) -> int:
    """Insert one exposure plan for a target (resolving its template). Mirrors the
    plan INSERT in :func:`write_project`; shared by the in-place update path."""
    if p.exposure_template_id is not None:
        template_id = p.exposure_template_id
    elif p.filter_name:
        template_id = _ensure_template(conn, profile_id, p.filter_name, templates)
    else:
        raise ValueError("exposure plan needs either exposure_template_id or filter_name")
    ecur = conn.execute(
        "INSERT INTO exposureplan"
        " (profileId, exposure, desired, acquired, accepted, targetid,"
        "  exposureTemplateId, enabled, guid)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            profile_id, p.exposure, p.desired, p.acquired, p.accepted, target_id,
            template_id, 1 if p.enabled else 0, _guid(),
        ),
    )
    pid = int(ecur.lastrowid)
    plan_ids.append(pid)
    return pid


def _insert_target(
    conn: sqlite3.Connection,
    project_id: int,
    profile_id: str,
    t: TargetSpec,
    templates: dict[str, int],
    plan_ids: list[int],
) -> int:
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
    tid = int(tcur.lastrowid)
    for p in t.exposure_plans:
        _insert_plan(conn, profile_id, tid, p, templates, plan_ids)
    return tid


def _reconcile_plans(
    conn: sqlite3.Connection,
    profile_id: str,
    target_id: int,
    plans: list[ExposurePlanSpec],
    templates: dict[str, int],
    plan_ids: list[int],
) -> None:
    """Make ``target_id``'s exposure plans match ``plans``, keyed by template Id:
    UPDATE matching plans (exposure/desired), INSERT new, DELETE removed. Existing
    plan rows (and their Ids) are preserved where the template is unchanged."""
    existing = {
        etid: pid
        for (pid, etid) in conn.execute(
            "SELECT Id, exposureTemplateId FROM exposureplan WHERE targetid = ?", (target_id,)
        )
    }
    desired: set[int] = set()
    for p in plans:
        etid = p.exposure_template_id
        if etid is not None and etid in existing:
            conn.execute(
                "UPDATE exposureplan SET exposure = ?, desired = ?, enabled = ? WHERE Id = ?",
                (p.exposure, p.desired, 1 if p.enabled else 0, existing[etid]),
            )
            plan_ids.append(existing[etid])
            desired.add(etid)
        else:
            _insert_plan(conn, profile_id, target_id, p, templates, plan_ids)
            if etid is not None:
                desired.add(etid)
    for etid, pid in existing.items():
        if etid not in desired:
            conn.execute("DELETE FROM exposureplan WHERE Id = ?", (pid,))


def update_project(
    conn: sqlite3.Connection, project_id: int, spec: ProjectSpec
) -> WriteResult:
    """In-place edit of an existing project (o2c). Does not commit.

    The caller MUST have verified the project is editable (Draft, no captured
    progress, no filter-cadence/override-order rows). Project + target **Ids stay
    stable** so target-keyed rows (flathistory, etc.) are preserved; only changed
    columns/plans are touched. Mirrors the create path's INSERT shapes.
    """
    # Apply the editable fields + advanced settings from the spec (psq). The spec
    # always carries non-null values, so this also self-heals any NINA-required column
    # that was NULL (bead nil) — a NULL crashes Target Scheduler's project load.
    # createdate is preserved (COALESCE keeps the original; healed only if NULL) since
    # it's the project's creation time, not an editable field.
    conn.execute(
        "UPDATE project SET name = ?, description = ?, priority = ?,"
        " createdate = COALESCE(createdate, ?),"
        " minimumtime = ?, minimumaltitude = ?, usecustomhorizon = ?, horizonoffset = ?,"
        " meridianwindow = ?, filterswitchfrequency = ?, ditherevery = ?, enablegrader = ?,"
        " maximumAltitude = ?, flatsHandling = ?, smartexposureorder = ?"
        " WHERE Id = ?",
        (
            spec.name,
            spec.description,
            spec.priority,
            int(time.time()),  # createdate (only used if currently NULL)
            spec.minimum_time,
            spec.minimum_altitude,
            1 if spec.use_custom_horizon else 0,
            spec.horizon_offset,
            spec.meridian_window,
            spec.filter_switch_frequency,
            spec.dither_every,
            1 if spec.enable_grader else 0,
            spec.maximum_altitude,
            spec.flats_handling,
            1 if spec.smart_exposure_order else 0,
            project_id,
        ),
    )

    # Rule weights: set each provided weight by name; ensure all 8 NINA rules exist
    # (crash-safety, bead nil). When spec carries none, existing weights are untouched.
    existing_rules = {
        name: rid
        for (rid, name) in conn.execute(
            "SELECT Id, name FROM ruleweight WHERE projectid = ?", (project_id,)
        )
    }
    ruleweight_ids: list[int] = list(existing_rules.values())
    desired_weights = {rw.name: rw.weight for rw in (spec.rule_weights or [])}
    for rname, rdefault in DEFAULT_RULE_WEIGHTS:
        if rname not in desired_weights and rname not in existing_rules:
            desired_weights[rname] = rdefault  # backfill a missing default rule
    for rname, rweight in desired_weights.items():
        if rname in existing_rules:
            conn.execute(
                "UPDATE ruleweight SET weight = ? WHERE Id = ?", (rweight, existing_rules[rname])
            )
        else:
            rcur = conn.execute(
                "INSERT INTO ruleweight (name, weight, projectid) VALUES (?, ?, ?)",
                (rname, rweight, project_id),
            )
            ruleweight_ids.append(int(rcur.lastrowid))

    # Targets reconciled by Id: UPDATE existing, INSERT new, DELETE removed.
    existing_targets = {
        tid for (tid,) in conn.execute("SELECT Id FROM target WHERE projectid = ?", (project_id,))
    }
    templates: dict[str, int] = {}
    target_ids: list[int] = []
    plan_ids: list[int] = []
    seen: set[int] = set()
    for t in spec.targets:
        if t.id is not None and t.id in existing_targets:
            conn.execute(
                "UPDATE target SET name = ?, ra = ?, dec = ?, rotation = ?, roi = ? WHERE Id = ?",
                (t.name, t.ra_deg / 15.0, t.dec_deg, t.rotation, t.roi, t.id),
            )
            seen.add(t.id)
            _reconcile_plans(conn, spec.profile_id, t.id, t.exposure_plans, templates, plan_ids)
            target_ids.append(t.id)
        else:
            target_ids.append(
                _insert_target(conn, project_id, spec.profile_id, t, templates, plan_ids)
            )

    for old in existing_targets - seen:
        conn.execute("DELETE FROM exposureplan WHERE targetid = ?", (old,))
        for tbl, col in (("flathistory", "targetId"), ("overrideexposureorderitem", "targetid"),
                         ("filtercadenceitem", "targetid")):
            if _table_exists(conn, tbl):
                conn.execute(f"DELETE FROM {tbl} WHERE {col} = ?", (old,))
        conn.execute("DELETE FROM target WHERE Id = ?", (old,))

    # Override exposure order (awh) is project-level: rebuild it on every kept/new target.
    # Also drop stale filter cadence — NINA regenerates it from filterswitchfrequency, and
    # leaving rows whose referenceIdx no longer matches the edited plans would be wrong.
    for tid in target_ids:
        if _table_exists(conn, "overrideexposureorderitem"):
            conn.execute("DELETE FROM overrideexposureorderitem WHERE targetid = ?", (tid,))
            _insert_override_order(conn, tid, spec.override_exposure_order)
        if _table_exists(conn, "filtercadenceitem"):
            conn.execute("DELETE FROM filtercadenceitem WHERE targetid = ?", (tid,))

    return WriteResult(
        project_id=project_id,
        target_ids=target_ids,
        plan_ids=plan_ids,
        template_ids=templates,
        ruleweight_ids=ruleweight_ids,
    )


def delete_project(conn: sqlite3.Connection, project_id: int) -> dict[str, int]:
    """Delete a project and all of its rows. Does not commit; the caller must have
    verified the project is editable (Draft, no progress, no cadence/override).

    Leaves shared exposuretemplates alone (they're user-owned and referenced by other
    projects). Returns per-table delete counts.
    """
    tids = [r[0] for r in conn.execute("SELECT Id FROM target WHERE projectid = ?", (project_id,))]
    deleted: dict[str, int] = {}
    for old in tids:
        c = conn.execute("DELETE FROM exposureplan WHERE targetid = ?", (old,))
        deleted["exposureplan"] = deleted.get("exposureplan", 0) + c.rowcount
        for tbl, col in (("flathistory", "targetId"), ("overrideexposureorderitem", "targetid"),
                         ("filtercadenceitem", "targetid")):
            if _table_exists(conn, tbl):
                conn.execute(f"DELETE FROM {tbl} WHERE {col} = ?", (old,))
    deleted["target"] = conn.execute(
        "DELETE FROM target WHERE projectid = ?", (project_id,)
    ).rowcount
    deleted["ruleweight"] = conn.execute(
        "DELETE FROM ruleweight WHERE projectid = ?", (project_id,)
    ).rowcount
    deleted["project"] = conn.execute(
        "DELETE FROM project WHERE Id = ?", (project_id,)
    ).rowcount
    return deleted


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
