"""Backup + transactional additive-only writer (bead mh3.2).

Builds a "pre-existing" Target Scheduler DB (simulating the user's), exports a new
project into it, and proves: backups coalesce by time, rollback leaves the DB
byte-identical, writes are strictly additive, provenance/undo remove exactly our
rows, the progress gate protects started work, RA stays in hours, pruning honors
retention, and the live-write/lock guards fire.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.db import backup, export, ops, provenance
from app.db.export import (
    DatabaseBusyError,
    EditNotAllowedError,
    ExportError,
    ProgressError,
    create_exposure_template,
    export_project,
    undo_operation,
    update_project,
)
from app.db.validate import ValidationError
from app.db.writer import (
    ExposurePlanSpec,
    ExposureTemplateSpec,
    ProjectSpec,
    RuleWeightSpec,
    TargetSpec,
    create_scheduler_db,
    write_project,
)

PROFILE = "11111111-1111-1111-1111-111111111111"
T0 = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Redirect backup dir + ops file to tmp; default to 'no database configured'.

    Every test passes an explicit ``target_db``, so the default resolution isn't used;
    point ``find_source_db`` at nothing so it never globs the developer's real DB.
    """
    bdir = tmp_path / "backups"
    bdir.mkdir()
    monkeypatch.setattr(backup, "BACKUP_DIR", bdir)
    monkeypatch.setattr(ops, "OPS_FILE", tmp_path / "ops.json")
    monkeypatch.setattr(export, "find_source_db", lambda: None)
    monkeypatch.setattr(export, "CONNECT_TIMEOUT", 0.2)


def _baseline(path):
    """A DB with pre-existing (NINA-style) content and NO provenance table."""
    conn = create_scheduler_db(path)
    write_project(
        conn,
        ProjectSpec(
            profile_id=PROFILE,
            name="Existing NINA project",
            targets=[
                TargetSpec(
                    name="Old target",
                    ra_deg=120.0,
                    dec_deg=20.0,
                    exposure_plans=[ExposurePlanSpec(filter_name="L", exposure=120.0)],
                )
            ],
        ),
    )
    conn.commit()
    conn.close()
    return path


def _new_project():
    return ProjectSpec(
        profile_id=PROFILE,
        name="NA Mosaic",
        is_mosaic=True,
        state=1,
        targets=[
            TargetSpec(
                name=f"Panel {i}",
                ra_deg=315.0,
                dec_deg=44.0 + i,
                rotation=10.0,
                exposure_plans=[
                    ExposurePlanSpec(filter_name=f, exposure=300.0, desired=30)
                    for f in ("Ha", "OIII")
                ],
            )
            for i in range(2)
        ],
    )


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(conn, table):
    conn.row_factory = sqlite3.Row
    return {r["Id"]: dict(r) for r in conn.execute(f"SELECT * FROM {table}")}


# --- backups ---------------------------------------------------------------

def test_backup_before_every_write(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    b1 = backup.ensure_backup(db, now=T0)
    b2 = backup.ensure_backup(db, now=T0 + timedelta(seconds=1))
    # A fresh restore point per write — no coalescing.
    assert b2.path != b1.path
    assert len(list(backup.BACKUP_DIR.glob("*.sqlite"))) == 2


def test_pruning_keeps_last_k_and_recent(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    # 6 backups, one per day going back (d days old).
    for d in range(6):
        c = sqlite3.connect(db)
        c.execute(f"PRAGMA user_version = {d}")  # mutate so each is a distinct backup
        c.commit()
        c.close()
        backup.create_backup(db, now=T0 - timedelta(days=d))
    removed = backup.prune_backups(db, keep_last=2, keep_days=3, now=T0)
    # last-2 {d0,d1} ∪ within-3-days {d0,d1,d2,d3} => 4 kept, {d4,d5} removed.
    assert len(list(backup.BACKUP_DIR.glob("*.sqlite"))) == 4
    assert len(removed) == 2
    assert backup.latest_backup(db) is not None


# --- transactional write ---------------------------------------------------

def test_rollback_leaves_db_byte_identical(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    before = _sha(db)

    def boom(conn, spec):
        raise ValidationError("nope")

    with pytest.raises(ValidationError):
        export_project(_new_project(), target_db=db, validate=boom, now=T0)
    assert _sha(db) == before  # no provenance table, no rows, identical bytes


def test_additive_guarantee(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    conn = sqlite3.connect(db)
    base_project = _rows(conn, "project")
    base_target = _rows(conn, "target")
    base_plan = _rows(conn, "exposureplan")
    conn.close()

    res = export_project(_new_project(), target_db=db, now=T0)

    conn = sqlite3.connect(db)
    after_project = _rows(conn, "project")
    after_target = _rows(conn, "target")
    # pre-existing rows are byte-for-byte unchanged
    for pid, row in base_project.items():
        assert after_project[pid] == row
    for tid, row in base_target.items():
        assert after_target[tid] == row
    # counts grew by exactly our additions
    assert len(after_project) == len(base_project) + 1
    assert len(after_target) == len(base_target) + 2
    assert len(_rows(conn, "exposureplan")) == len(base_plan) + 4
    # our new ids are all greater than the prior max
    assert res.project_id > max(base_project)
    assert min(res.target_ids) > max(base_target)
    conn.close()


def test_plan_references_existing_template(tmp_path):
    """qiz.1 picker: a plan with exposure_template_id reuses that template — no new
    bare template is created, and the plan points at the referenced Id."""
    db = _baseline(tmp_path / "t.sqlite")
    conn = sqlite3.connect(db)
    base_templates = _rows(conn, "exposuretemplate")
    conn.close()
    assert base_templates, "baseline should have the 'L' template to reference"
    existing_id = next(iter(base_templates))

    spec = ProjectSpec(
        profile_id=PROFILE,
        name="Refs existing template",
        targets=[
            TargetSpec(
                name="T1",
                ra_deg=10.0,
                dec_deg=10.0,
                exposure_plans=[
                    ExposurePlanSpec(exposure_template_id=existing_id, exposure=300.0, desired=5)
                ],
            )
        ],
    )
    res = export_project(spec, target_db=db, now=T0)

    conn = sqlite3.connect(db)
    after_templates = _rows(conn, "exposuretemplate")
    # no new template row, and the referenced one is byte-identical
    assert len(after_templates) == len(base_templates)
    assert after_templates[existing_id] == base_templates[existing_id]
    # the plan we wrote points at the existing template
    plan_tmpl = conn.execute(
        "SELECT exposureTemplateId FROM exposureplan WHERE Id = ?", (res.plan_ids[0],)
    ).fetchone()[0]
    conn.close()
    assert plan_tmpl == existing_id
    assert res.template_ids == {}  # we created/own no templates this op


def test_create_exposure_template_additive(tmp_path):
    """qiz.5: creating a template adds exactly one full-column row, provenanced,
    additive, and is undoable via the existing undo path."""
    db = _baseline(tmp_path / "t.sqlite")
    conn = sqlite3.connect(db)
    before = _rows(conn, "exposuretemplate")
    conn.close()

    spec = ExposureTemplateSpec(
        profile_id=PROFILE,
        name="Ha 3nm 900s",
        filter_name="Ha",
        gain=120,
        offset=30,
        binning=1,
        moon_avoidance_enabled=True,
        moon_avoidance_separation=120.0,
        default_exposure=900.0,
        dither_every=1,
    )
    res = create_exposure_template(spec, target_db=db, now=T0)

    conn = sqlite3.connect(db)
    after = _rows(conn, "exposuretemplate")
    assert len(after) == len(before) + 1  # exactly one new row
    assert res.template_id > max(before)  # additive id
    row = after[res.template_id]
    # advanced + essential fields persisted
    assert row["name"] == "Ha 3nm 900s"
    assert row["gain"] == 120 and row["offset"] == 30
    assert row["moonavoidanceenabled"] == 1
    assert row["moonavoidanceseparation"] == pytest.approx(120.0)
    assert row["defaultexposure"] == pytest.approx(900.0)
    assert row["ditherevery"] == 1
    assert row["guid"]
    # NINA defaults for untouched advanced fields
    assert row["moonrelaxmaxaltitude"] == pytest.approx(5.0)
    assert row["minutesOffset"] == 0
    # baseline rows untouched
    for tid, r in before.items():
        assert after[tid] == r
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()

    # provenanced -> undoable via the generic undo path
    undo_operation(res.operation_id, target_db=db, now=T0)
    conn = sqlite3.connect(db)
    assert len(_rows(conn, "exposuretemplate")) == len(before)
    conn.close()


def test_create_exposure_template_default_sentinels(tmp_path):
    """A minimal create (name + filter only) stores NINA's -1 'use default'
    sentinel for gain/offset/readoutmode/ditherevery — matching real TS rows."""
    db = _baseline(tmp_path / "t.sqlite")
    res = create_exposure_template(
        ExposureTemplateSpec(profile_id=PROFILE, name="L default", filter_name="L"),
        target_db=db,
        now=T0,
    )
    conn = sqlite3.connect(db)
    row = _rows(conn, "exposuretemplate")[res.template_id]
    conn.close()
    assert row["gain"] == -1
    assert row["offset"] == -1
    assert row["readoutmode"] == -1
    assert row["ditherevery"] == -1
    assert row["bin"] == 1
    assert row["defaultexposure"] == pytest.approx(60.0)


def test_create_exposure_template_rejects_blank(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    before = _sha(db)
    with pytest.raises(ValidationError):
        create_exposure_template(
            ExposureTemplateSpec(profile_id=PROFILE, name="", filter_name="Ha"),
            target_db=db,
            now=T0,
        )
    assert _sha(db) == before  # rolled back, byte-identical


def test_ra_persisted_in_hours_through_wrapper(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    export_project(_new_project(), target_db=db, now=T0)
    conn = sqlite3.connect(db)
    ra = conn.execute("SELECT ra FROM target WHERE name = ?", ("Panel 0",)).fetchone()[0]
    conn.close()
    assert ra == pytest.approx(315.0 / 15.0, abs=1e-9)


def test_foreign_key_integrity(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    export_project(_new_project(), target_db=db, now=T0)
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_project_required_columns_not_null(tmp_path):
    """Regression (bead nil): these project columns are nullable in SQLite but
    non-nullable in NINA's EF model. A NULL here makes NINA's projects query throw
    and ALL projects vanish, so the writer must populate every one of them."""
    db = _baseline(tmp_path / "t.sqlite")
    res = export_project(_new_project(), target_db=db, now=T0)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM project WHERE Id = ?", (res.project_id,)
    ).fetchone()
    conn.close()
    required = [
        "createdate", "minimumtime", "minimumaltitude", "usecustomhorizon",
        "horizonoffset", "meridianwindow", "filterswitchfrequency", "ditherevery",
        "enablegrader",
    ]
    nulls = [c for c in required if row[c] is None]
    assert not nulls, f"NINA-required project columns left NULL: {nulls}"


def test_project_seeds_default_ruleweights(tmp_path):
    """Regression (bead nil): every NINA project has 8 scoring ruleweights; a project
    with none crashes Target Scheduler. The writer must seed NINA's defaults."""
    from app.db.writer import DEFAULT_RULE_WEIGHTS

    db = _baseline(tmp_path / "t.sqlite")
    res = export_project(_new_project(), target_db=db, now=T0)
    conn = sqlite3.connect(db)
    got = dict(
        conn.execute(
            "SELECT name, weight FROM ruleweight WHERE projectid = ?", (res.project_id,)
        ).fetchall()
    )
    conn.close()
    assert got == {name: weight for name, weight in DEFAULT_RULE_WEIGHTS}
    assert len(got) == 8


def test_custom_rule_weights_override_defaults(tmp_path):
    """qiz.3: provided weights override matching rules; all 8 still written."""
    from app.db.writer import DEFAULT_RULE_WEIGHTS, RuleWeightSpec

    db = _baseline(tmp_path / "t.sqlite")
    spec = _new_project()
    spec.rule_weights = [
        RuleWeightSpec(name="Project Priority", weight=99.0),
        RuleWeightSpec(name="Mosaic Completion", weight=42.0),
        RuleWeightSpec(name="Not A Real Rule", weight=7.0),  # ignored, not seeded
    ]
    res = export_project(spec, target_db=db, now=T0)
    conn = sqlite3.connect(db)
    got = dict(
        conn.execute(
            "SELECT name, weight FROM ruleweight WHERE projectid = ?", (res.project_id,)
        ).fetchall()
    )
    conn.close()
    assert len(got) == 8  # crash-safety invariant: full set always written
    assert got["Project Priority"] == 99.0
    assert got["Mosaic Completion"] == 42.0
    assert "Not A Real Rule" not in got
    # unspecified rules keep their NINA default
    defaults = dict(DEFAULT_RULE_WEIGHTS)
    assert got["Setting Soonest"] == defaults["Setting Soonest"]


def test_rule_weight_defaults_endpoint():
    from fastapi.testclient import TestClient

    from app.db.writer import DEFAULT_RULE_WEIGHTS
    from app.main import app

    body = TestClient(app).get("/api/rule-weight-defaults").json()
    assert [(r["name"], r["weight"]) for r in body] == list(DEFAULT_RULE_WEIGHTS)


# --- o2c: in-place edit of Draft projects ----------------------------------


def _export_draft(db):
    """Export a fresh Draft (state=0) single-target project; return (res, conn-read helpers)."""
    spec = ProjectSpec(
        profile_id=PROFILE,
        name="Draft",
        state=0,
        targets=[
            TargetSpec(
                name="T1",
                ra_deg=120.0,
                dec_deg=20.0,
                exposure_plans=[ExposurePlanSpec(filter_name="L", exposure=120.0, desired=10)],
            )
        ],
    )
    return export_project(spec, target_db=db, now=T0)


def _first_target_and_plan(db, project_id):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    tgt = conn.execute(
        "SELECT Id FROM target WHERE projectid = ?", (project_id,)
    ).fetchone()["Id"]
    plan = conn.execute(
        "SELECT Id, exposureTemplateId, desired FROM exposureplan WHERE targetid = ?", (tgt,)
    ).fetchone()
    conn.close()
    return tgt, plan["Id"], plan["exposureTemplateId"]


def test_update_project_in_place(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    res = _export_draft(db)
    pid = res.project_id
    tgt, plan_id, etid = _first_target_and_plan(db, pid)

    spec = ProjectSpec(
        profile_id=PROFILE,
        name="Draft EDITED",
        state=0,
        rule_weights=[RuleWeightSpec(name="Project Priority", weight=88.0)],
        targets=[
            TargetSpec(
                id=tgt,
                name="T1b",
                ra_deg=130.0,
                dec_deg=25.0,
                exposure_plans=[
                    ExposurePlanSpec(exposure=120.0, desired=25, exposure_template_id=etid)
                ],
            )
        ],
    )
    out = update_project(pid, spec, target_db=db, now=T0)
    assert out.project_id == pid  # same project, edited in place

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT name FROM project WHERE Id = ?", (pid,)).fetchone()["name"] == "Draft EDITED"
    trows = conn.execute("SELECT Id, name FROM target WHERE projectid = ?", (pid,)).fetchall()
    assert len(trows) == 1 and trows[0]["Id"] == tgt and trows[0]["name"] == "T1b"  # Id stable
    prow = conn.execute("SELECT Id, desired FROM exposureplan WHERE targetid = ?", (tgt,)).fetchone()
    assert prow["Id"] == plan_id and prow["desired"] == 25  # plan updated in place
    w = conn.execute(
        "SELECT weight FROM ruleweight WHERE projectid = ? AND name = 'Project Priority'", (pid,)
    ).fetchone()["weight"]
    assert w == 88.0
    # crash-safety invariant preserved
    assert conn.execute("SELECT COUNT(*) FROM ruleweight WHERE projectid = ?", (pid,)).fetchone()[0] == 8
    conn.close()


def test_update_reconciles_added_and_removed_targets(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    pid = _export_draft(db).project_id
    tgt, _plan_id, etid = _first_target_and_plan(db, pid)

    # Keep the existing target; add a brand-new one (no id).
    spec = ProjectSpec(
        profile_id=PROFILE,
        name="Draft",
        state=0,
        targets=[
            TargetSpec(id=tgt, name="T1", ra_deg=120.0, dec_deg=20.0,
                       exposure_plans=[ExposurePlanSpec(exposure=120.0, desired=10, exposure_template_id=etid)]),
            TargetSpec(name="T2 new", ra_deg=200.0, dec_deg=10.0,
                       exposure_plans=[ExposurePlanSpec(exposure=60.0, desired=5, exposure_template_id=etid)]),
        ],
    )
    update_project(pid, spec, target_db=db, now=T0)
    conn = sqlite3.connect(db)
    names = {r[0] for r in conn.execute("SELECT name FROM target WHERE projectid = ?", (pid,))}
    assert names == {"T1", "T2 new"}

    # Now remove the new one again.
    spec.targets = [spec.targets[0]]
    update_project(pid, spec, target_db=db, now=T0)
    names = {r[0] for r in conn.execute("SELECT name FROM target WHERE projectid = ?", (pid,))}
    assert names == {"T1"}
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_update_preserves_flathistory(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    pid = _export_draft(db).project_id
    tgt, _plan_id, etid = _first_target_and_plan(db, pid)

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO flathistory (targetId, profileId, flatsType, filterName) VALUES (?, ?, 'SKY', 'L')",
        (tgt, PROFILE),
    )
    conn.commit()
    conn.close()

    spec = ProjectSpec(
        profile_id=PROFILE, name="Draft", state=0,
        targets=[TargetSpec(id=tgt, name="T1", ra_deg=120.0, dec_deg=20.0,
                            exposure_plans=[ExposurePlanSpec(exposure=120.0, desired=99, exposure_template_id=etid)])],
    )
    update_project(pid, spec, target_db=db, now=T0)

    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM flathistory WHERE targetId = ?", (tgt,)).fetchone()[0]
    conn.close()
    assert n == 1  # target Id stable → flat history survives the edit


def test_update_backfills_null_required_columns(tmp_path):
    """Editing self-heals a NULL NINA-required column (e.g. createDate) so the edited
    project loads in NINA instead of crashing (bead nil class)."""
    db = _baseline(tmp_path / "t.sqlite")
    pid = _export_draft(db).project_id
    tgt, _plan_id, etid = _first_target_and_plan(db, pid)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE project SET createdate = NULL WHERE Id = ?", (pid,))
    conn.commit()
    conn.close()

    spec = ProjectSpec(profile_id=PROFILE, name="Draft", state=0,
                       targets=[TargetSpec(id=tgt, name="T1", ra_deg=120.0, dec_deg=20.0,
                                           exposure_plans=[ExposurePlanSpec(exposure=120.0, desired=1, exposure_template_id=etid)])])
    update_project(pid, spec, target_db=db, now=T0)

    conn = sqlite3.connect(db)
    cd = conn.execute("SELECT createdate FROM project WHERE Id = ?", (pid,)).fetchone()[0]
    conn.close()
    assert cd is not None  # repaired, not preserved-as-NULL


def test_update_refuses_non_draft(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    pid = export_project(_new_project(), target_db=db, now=T0).project_id  # state=1 (active)
    with pytest.raises(EditNotAllowedError):
        update_project(pid, _new_project(), target_db=db, now=T0)


def test_update_refuses_with_progress(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    pid = _export_draft(db).project_id
    tgt, _plan_id, etid = _first_target_and_plan(db, pid)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE exposureplan SET acquired = 3 WHERE targetid = ?", (tgt,))
    conn.commit()
    conn.close()
    spec = ProjectSpec(profile_id=PROFILE, name="x", state=0,
                       targets=[TargetSpec(id=tgt, name="T1", ra_deg=120.0, dec_deg=20.0,
                                           exposure_plans=[ExposurePlanSpec(exposure=120.0, desired=1, exposure_template_id=etid)])])
    with pytest.raises(ProgressError):
        update_project(pid, spec, target_db=db, now=T0)


def test_update_refuses_with_filter_cadence(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    pid = _export_draft(db).project_id
    tgt, _plan_id, etid = _first_target_and_plan(db, pid)
    conn = sqlite3.connect(db)
    conn.execute(
        'INSERT INTO filtercadenceitem (targetid, "order", action) VALUES (?, 1, 0)', (tgt,)
    )
    conn.commit()
    conn.close()
    spec = ProjectSpec(profile_id=PROFILE, name="x", state=0,
                       targets=[TargetSpec(id=tgt, name="T1", ra_deg=120.0, dec_deg=20.0,
                                           exposure_plans=[ExposurePlanSpec(exposure=120.0, desired=1, exposure_template_id=etid)])])
    with pytest.raises(EditNotAllowedError):
        update_project(pid, spec, target_db=db, now=T0)


def test_update_refuses_unknown_project(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    spec = ProjectSpec(
        profile_id=PROFILE, name="x", state=0,
        targets=[TargetSpec(name="T", ra_deg=1.0, dec_deg=1.0,
                            exposure_plans=[ExposurePlanSpec(filter_name="L", exposure=1.0)])],
    )
    with pytest.raises(EditNotAllowedError):
        update_project(99999, spec, target_db=db, now=T0)


# --- provenance + undo -----------------------------------------------------

def test_provenance_records_our_rows(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    res = export_project(_new_project(), target_db=db, now=T0)
    conn = sqlite3.connect(db)
    prov = provenance.rows_for_operation(conn, res.operation_id)
    conn.close()
    by_table = {}
    for r in prov:
        by_table.setdefault(r.table, []).append(r.id)
    assert by_table["project"] == [res.project_id]
    assert sorted(by_table["target"]) == sorted(res.target_ids)
    assert sorted(by_table["exposureplan"]) == sorted(res.plan_ids)
    assert len(by_table["ruleweight"]) == 8  # NINA's 8 default scoring rules
    # every provenance row carries a guid EXCEPT guidless tables (ruleweight)
    assert all(r.guid for r in prov if r.table != "ruleweight")
    assert all(r.guid is None for r in prov if r.table == "ruleweight")


def test_undo_removes_exactly_our_rows(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    conn = sqlite3.connect(db)
    base = {
        t: len(_rows(conn, t))
        for t in ("project", "target", "exposureplan", "exposuretemplate", "ruleweight")
    }
    conn.close()

    res = export_project(_new_project(), target_db=db, now=T0)
    undo = undo_operation(res.operation_id, now=T0 + timedelta(minutes=1))

    conn = sqlite3.connect(db)
    after = {t: len(_rows(conn, t)) for t in base}
    assert after == base  # back to baseline exactly (ruleweights removed too)
    assert provenance.rows_for_operation(conn, res.operation_id) == []
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []  # no orphaned rules
    conn.close()
    assert undo.deleted["project"] == 1
    assert undo.deleted["target"] == 2
    assert undo.deleted["ruleweight"] == 8  # one project's worth of default rules


def test_undo_aborts_on_guid_mismatch(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    res = export_project(_new_project(), target_db=db, now=T0)
    # Tamper one of our target guids so it's "no longer ours".
    conn = sqlite3.connect(db)
    conn.execute("UPDATE target SET guid = 'tampered' WHERE Id = ?", (res.target_ids[0],))
    conn.commit()
    before_counts = {t: len(_rows(conn, t)) for t in ("project", "target", "exposureplan")}
    conn.close()

    with pytest.raises(ExportError):
        undo_operation(res.operation_id, now=T0 + timedelta(minutes=1))

    conn = sqlite3.connect(db)
    after_counts = {t: len(_rows(conn, t)) for t in before_counts}
    assert after_counts == before_counts  # aborted -> nothing deleted
    conn.close()


def test_progress_gate_blocks_undo(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    res = export_project(_new_project(), target_db=db, now=T0)
    # Simulate captured progress on one of our targets.
    conn = sqlite3.connect(db)
    conn.execute("UPDATE exposureplan SET acquired = 5 WHERE targetid = ?", (res.target_ids[0],))
    conn.commit()
    counts = {t: len(_rows(conn, t)) for t in ("project", "target", "exposureplan")}
    conn.close()

    with pytest.raises(ProgressError):
        undo_operation(res.operation_id, now=T0 + timedelta(minutes=1))

    conn = sqlite3.connect(db)
    assert {t: len(_rows(conn, t)) for t in counts} == counts  # nothing removed
    conn.close()


# --- guards ----------------------------------------------------------------

def test_incompatible_schema_rejected_and_rolled_back(tmp_path):
    from app.db.validate import ValidationError

    db = _baseline(tmp_path / "t.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE target DROP COLUMN guid")  # incompatible: writer fills guid
    conn.commit()
    conn.close()
    before = _sha(db)

    with pytest.raises(ValidationError):
        export_project(_new_project(), target_db=db, now=T0)
    assert _sha(db) == before  # pre-write gate -> nothing inserted, byte-identical


def test_busy_db_fails_fast(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # hold the write lock
    try:
        with pytest.raises(DatabaseBusyError):
            export_project(_new_project(), target_db=db, now=T0)
    finally:
        holder.rollback()
        holder.close()
    # DB unchanged: our project never landed.
    conn = sqlite3.connect(db)
    names = [r[0] for r in conn.execute("SELECT name FROM project")]
    conn.close()
    assert names == ["Existing NINA project"]
