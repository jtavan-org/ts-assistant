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
    ExportError,
    ProgressError,
    export_project,
    undo_operation,
)
from app.db.validate import ValidationError
from app.db.writer import (
    ExposurePlanSpec,
    ProjectSpec,
    TargetSpec,
    create_scheduler_db,
    write_project,
)

PROFILE = "11111111-1111-1111-1111-111111111111"
T0 = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Redirect backup dir + ops file to tmp, and default to 'no live source DB'."""
    bdir = tmp_path / "backups"
    bdir.mkdir()
    monkeypatch.setattr(backup, "BACKUP_DIR", bdir)
    monkeypatch.setattr(ops, "OPS_FILE", tmp_path / "ops.json")
    monkeypatch.setattr(export, "EXPORT_DB", tmp_path / "export.sqlite")
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

def test_backup_coalescing(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    b1 = backup.ensure_backup(db, window_min=15, now=T0)
    # within window, file unchanged -> reuse
    b2 = backup.ensure_backup(db, window_min=15, now=T0 + timedelta(minutes=5))
    assert b2.path == b1.path
    assert len(list(backup.BACKUP_DIR.glob("*.sqlite"))) == 1

    # window expired -> new backup
    b3 = backup.ensure_backup(db, window_min=15, now=T0 + timedelta(minutes=20))
    assert b3.path != b1.path

    # external change within window -> new backup
    c = sqlite3.connect(db)
    c.execute("PRAGMA user_version = 99")
    c.commit()
    c.close()
    b4 = backup.ensure_backup(db, window_min=15, now=T0 + timedelta(minutes=21))
    assert b4.path != b3.path
    assert len(list(backup.BACKUP_DIR.glob("*.sqlite"))) == 3


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
    assert all(r.guid for r in prov)  # every provenance row carries a guid


def test_undo_removes_exactly_our_rows(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    conn = sqlite3.connect(db)
    base = {t: len(_rows(conn, t)) for t in ("project", "target", "exposureplan", "exposuretemplate")}
    conn.close()

    res = export_project(_new_project(), target_db=db, now=T0)
    undo = undo_operation(res.operation_id, now=T0 + timedelta(minutes=1))

    conn = sqlite3.connect(db)
    after = {t: len(_rows(conn, t)) for t in base}
    assert after == base  # back to baseline exactly
    assert provenance.rows_for_operation(conn, res.operation_id) == []
    conn.close()
    assert undo.deleted["project"] == 1
    assert undo.deleted["target"] == 2


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

def test_live_write_guard(tmp_path, monkeypatch):
    db = _baseline(tmp_path / "live.sqlite")
    # Make the target look like the live source DB.
    monkeypatch.setattr(export, "find_source_db", lambda: db)

    monkeypatch.delenv("TS_ASSISTANT_ALLOW_LIVE_WRITE", raising=False)
    with pytest.raises(ExportError):
        export_project(_new_project(), target_db=db, now=T0)

    monkeypatch.setenv("TS_ASSISTANT_ALLOW_LIVE_WRITE", "1")
    res = export_project(_new_project(), target_db=db, now=T0)  # now allowed
    assert res.project_id > 0


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
