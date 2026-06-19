"""Read Target Scheduler projects/targets/exposure plans from a working copy.

Column access is case-insensitive and tolerant of missing columns so the reader
survives schema drift across Target Scheduler versions. The canonical (current)
column names are documented inline.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .introspect import introspect
from .models import (
    EPOCH_CODE,
    PROJECT_STATE,
    ExposurePlan,
    ExposureTemplate,
    Project,
    SchemaInfo,
    Target,
)
from .working_copy import connect_readonly, get_working_copy


def _row_get(row: sqlite3.Row, *candidates: str, default: Any = None) -> Any:
    """Fetch a column value by any of several candidate names, case-insensitively."""
    keys = {k.lower(): k for k in row.keys()}
    for cand in candidates:
        actual = keys.get(cand.lower())
        if actual is not None:
            return row[actual]
    return default


def _find_table(tables: dict, *candidates: str) -> str | None:
    lower = {name.lower(): name for name in tables}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def _state_label(code: Any) -> str:
    try:
        return PROJECT_STATE.get(int(code), f"state:{int(code)}")
    except (TypeError, ValueError):
        return "draft"


def _epoch_label(code: Any) -> str:
    try:
        return EPOCH_CODE.get(int(code), f"epoch:{int(code)}")
    except (TypeError, ValueError):
        return "J2000"


def _template_filter_map(conn: sqlite3.Connection, table: str | None) -> dict[int, str]:
    """Map ExposureTemplate.Id -> filterName for resolving plan filter labels."""
    if not table:
        return {}
    out: dict[int, str] = {}
    for r in conn.execute(f'SELECT * FROM "{table}"'):
        tid = _row_get(r, "Id", "id")
        fname = _row_get(r, "filterName", "filtername", "name")
        if tid is not None:
            out[int(tid)] = fname
    return out


def load_projects() -> list[Project]:
    """Load all projects with nested targets and exposure plans (working copy)."""
    wc = get_working_copy()
    if wc is None:
        return []
    with connect_readonly(wc) as conn:
        return load_projects_conn(conn)


def load_projects_conn(conn: sqlite3.Connection) -> list[Project]:
    """Load all projects (with nested targets + exposure plans) from a connection.

    Shared by the working-copy reader and by the writer/export round-trip tests so
    both exercise exactly the same projection logic. Sets the connection's
    row_factory to sqlite3.Row so columns can be looked up by name.
    """
    conn.row_factory = sqlite3.Row
    tables = introspect(conn)
    t_project = _find_table(tables, "project")
    t_target = _find_table(tables, "target")
    t_plan = _find_table(tables, "exposureplan", "exposure_plan")
    t_template = _find_table(tables, "exposuretemplate", "exposure_template")
    if not t_project or not t_target:
        return []

    filter_by_template = _template_filter_map(conn, t_template)

    # Exposure plans grouped by target id.
    plans_by_target: dict[int, list[ExposurePlan]] = {}
    if t_plan:
        for r in conn.execute(f'SELECT * FROM "{t_plan}"'):
            target_id = _row_get(r, "TargetId", "targetId", "target_id")
            if target_id is None:
                continue
            tmpl_id = _row_get(r, "ExposureTemplateId", "exposureTemplateId")
            plan = ExposurePlan(
                id=int(_row_get(r, "Id", "id")),
                filter_name=filter_by_template.get(
                    int(tmpl_id) if tmpl_id is not None else -1
                ),
                exposure=_row_get(r, "exposure"),
                desired=int(_row_get(r, "desired", default=0) or 0),
                acquired=int(_row_get(r, "acquired", default=0) or 0),
                accepted=int(_row_get(r, "accepted", default=0) or 0),
                exposure_template_id=int(tmpl_id) if tmpl_id is not None else None,
            )
            plans_by_target.setdefault(int(target_id), []).append(plan)

    # Projects, keyed by id, then attach targets.
    projects: dict[int, Project] = {}
    for r in conn.execute(f'SELECT * FROM "{t_project}"'):
        pid = int(_row_get(r, "Id", "id"))
        projects[pid] = Project(
            id=pid,
            name=_row_get(r, "name", default=f"Project {pid}"),
            description=_row_get(r, "description"),
            profile_id=_row_get(r, "ProfileId", "profileId"),
            state=_state_label(_row_get(r, "state", "state_col")),
            priority=_row_get(r, "priority", "priority_col"),
            is_mosaic=bool(_row_get(r, "isMosaic", default=0)),
        )

    for r in conn.execute(f'SELECT * FROM "{t_target}"'):
        project_id = _row_get(r, "ProjectId", "projectId", "project_id")
        if project_id is None or int(project_id) not in projects:
            continue
        proj = projects[int(project_id)]
        tid = int(_row_get(r, "Id", "id"))
        proj.targets.append(
            Target(
                id=tid,
                name=_row_get(r, "name", default=f"Target {tid}"),
                active=bool(_row_get(r, "active", default=1)),
                # Target Scheduler stores RA in HOURS (the EF Target entity
                # wraps it with Angle.ByHours); Dec is in degrees. Verified
                # empirically against known objects. Convert RA to degrees.
                ra_deg=float(_row_get(r, "ra", default=0.0) or 0.0) * 15.0,
                dec_deg=float(_row_get(r, "dec", default=0.0) or 0.0),
                rotation=float(_row_get(r, "rotation", default=0.0) or 0.0),
                roi=float(_row_get(r, "roi", default=100.0) or 100.0),
                epoch=_epoch_label(_row_get(r, "epochCode", "epoch")),
                project_id=proj.id,
                project_name=proj.name,
                exposure_plans=plans_by_target.get(tid, []),
            )
        )

    return list(projects.values())


def _opt_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _opt_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _opt_bool(v: Any) -> bool | None:
    return None if v is None else bool(v)


def load_exposure_templates_conn(conn: sqlite3.Connection) -> list[ExposureTemplate]:
    """Read all exposure templates (full columns) from a connection.

    Tolerant of missing columns so it survives Target Scheduler schema drift —
    any absent column projects to None.
    """
    conn.row_factory = sqlite3.Row
    tables = introspect(conn)
    t_template = _find_table(tables, "exposuretemplate", "exposure_template")
    if not t_template:
        return []
    out: list[ExposureTemplate] = []
    for r in conn.execute(f'SELECT * FROM "{t_template}"'):
        tid = _row_get(r, "Id", "id")
        if tid is None:
            continue
        out.append(
            ExposureTemplate(
                id=int(tid),
                profile_id=_row_get(r, "ProfileId", "profileId"),
                name=_row_get(r, "name", default=f"Template {int(tid)}"),
                filter_name=_row_get(r, "filterName", "filtername"),
                gain=_opt_int(_row_get(r, "gain")),
                offset=_opt_int(_row_get(r, "offset")),
                binning=_opt_int(_row_get(r, "bin", "binning")),
                readout_mode=_opt_int(_row_get(r, "readoutmode", "readoutMode")),
                twilight_level=_opt_int(_row_get(r, "twilightlevel", "twilightLevel")),
                moon_avoidance_enabled=_opt_bool(
                    _row_get(r, "moonavoidanceenabled", "moonAvoidanceEnabled")
                ),
                moon_avoidance_separation=_opt_float(
                    _row_get(r, "moonavoidanceseparation", "moonAvoidanceSeparation")
                ),
                moon_avoidance_width=_opt_int(
                    _row_get(r, "moonavoidancewidth", "moonAvoidanceWidth")
                ),
                maximum_humidity=_opt_float(_row_get(r, "maximumhumidity", "maximumHumidity")),
                default_exposure=_opt_float(_row_get(r, "defaultexposure", "defaultExposure")),
                dither_every=_opt_int(_row_get(r, "ditherevery", "ditherEvery")),
                minutes_offset=_opt_int(_row_get(r, "minutesOffset", "minutesoffset")),
            )
        )
    return out


def load_exposure_templates() -> list[ExposureTemplate]:
    """Load all exposure templates from the working copy (qiz.1 picker)."""
    wc = get_working_copy()
    if wc is None:
        return []
    with connect_readonly(wc) as conn:
        return load_exposure_templates_conn(conn)


def load_targets() -> list[Target]:
    """Flat list of all targets across projects (convenience for the sky overlay)."""
    return [t for p in load_projects() for t in p.targets]


def schema_info() -> SchemaInfo:
    from ..config import find_source_db

    src = find_source_db()
    wc = get_working_copy()
    if wc is None:
        return SchemaInfo(tables={}, source_db=None, db_present=False)
    with connect_readonly(wc) as conn:
        tables = introspect(conn)
    return SchemaInfo(
        tables={name: t.row_count for name, t in tables.items()},
        source_db=str(src) if src else None,
        db_present=True,
    )
