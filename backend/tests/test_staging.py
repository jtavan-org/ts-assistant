"""Local-staged writes (khf).

Writes are performed on a local working copy and published back to the source with an
optimistic compare-and-swap, so they work when the source lives on a network share /
file-sync replica. These tests cover the staging primitives and the concurrent-write
guard.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from app.db import backup, ops
from app.db import export as export_mod
import os

from app.db.export import (
    ConcurrentModificationError,
    DatabaseBusyError,
    DatabaseReadOnlyError,
    _publish,
    _stage_in,
    create_exposure_template,
    export_project,
)
from app.db.backup import backup_signature
from app.db.writer import (
    ExposurePlanSpec,
    ExposureTemplateSpec,
    ProjectSpec,
    TargetSpec,
    create_scheduler_db,
    write_project,
)

PROFILE = "11111111-1111-1111-1111-111111111111"
T0 = datetime(2026, 6, 21, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    (tmp_path / "backups").mkdir()
    monkeypatch.setattr(ops, "OPS_FILE", tmp_path / "ops.json")
    monkeypatch.setattr(export_mod, "find_source_db", lambda: None)
    monkeypatch.setattr(export_mod, "CONNECT_TIMEOUT", 0.2)


def _baseline(path):
    conn = create_scheduler_db(path)
    write_project(
        conn,
        ProjectSpec(
            profile_id=PROFILE,
            name="Existing",
            targets=[
                TargetSpec(
                    name="T",
                    ra_deg=10.0,
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
        name="Staged Project",
        targets=[
            TargetSpec(
                name="P0",
                ra_deg=315.0,
                dec_deg=44.0,
                exposure_plans=[ExposurePlanSpec(filter_name="Ha", exposure=300.0, desired=5)],
            )
        ],
    )


def _new_template():
    return ExposureTemplateSpec(profile_id=PROFILE, name="Staged Template", filter_name="Ha")


def _count(db, table):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_stage_in_copies_content_and_is_rollback_mode(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    work = _stage_in(db)
    try:
        assert work != db
        assert _count(work, "project") == _count(db, "project")
        conn = sqlite3.connect(str(work))
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
        finally:
            conn.close()
    finally:
        work.unlink(missing_ok=True)


def test_publish_replaces_source_and_returns_signature(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    sig0 = backup_signature(db)
    work = _stage_in(db)
    # mutate the working copy
    conn = sqlite3.connect(str(work))
    conn.execute("INSERT INTO project (profileId, name) VALUES (?, ?)", (PROFILE, "Added"))
    conn.commit()
    conn.close()

    new_sig = _publish(db, work, sig0)
    assert new_sig == backup_signature(db)
    assert "Added" in {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM project")}


def test_publish_detects_concurrent_modification(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    work = _stage_in(db)
    stale_sig = "size:1;mtime:1"  # not the source's real signature -> CAS must trip
    try:
        with pytest.raises(ConcurrentModificationError):
            _publish(db, work, stale_sig)
    finally:
        work.unlink(missing_ok=True)


def test_concurrent_external_write_aborts_publish(tmp_path):
    """If the source changes while we edit (NINA wrote), the staged result is refused
    rather than clobbering the external change."""
    db = _baseline(tmp_path / "t.sqlite")
    before = _count(db, "project")

    def meddle(conn, spec):
        # Simulate NINA writing the real DB mid-edit (validate runs in the work txn).
        ext = sqlite3.connect(db)
        ext.execute("UPDATE project SET name = name || '!'")
        ext.commit()
        ext.close()

    with pytest.raises(ConcurrentModificationError):
        export_project(_new_project(), target_db=db, now=T0, validate=meddle)

    # Our new project was NOT published; the external change is preserved.
    names = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM project")}
    assert "Staged Project" not in names
    assert _count(db, "project") == before


def test_concurrent_modification_is_a_busy_error():
    # Routers map DatabaseBusyError -> 409, so the conflict is retriable for the user.
    assert issubclass(ConcurrentModificationError, DatabaseBusyError)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission checks")
def test_publish_to_unwritable_dir_raises_readonly(tmp_path):
    """If the database's folder isn't writable, publish fails with a clear read-only
    error (mapped to 409) rather than a raw OSError."""
    dbdir = tmp_path / "dbdir"
    dbdir.mkdir()
    db = _baseline(dbdir / "t.sqlite")
    os.chmod(dbdir, 0o555)  # readable (so staging can read) but not writable
    try:
        with pytest.raises(DatabaseReadOnlyError):
            export_project(_new_project(), target_db=db, now=T0)
    finally:
        os.chmod(dbdir, 0o755)


def test_staged_write_succeeds_and_persists(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    before = _count(db, "project")
    res = export_project(_new_project(), target_db=db, now=T0)
    assert res.project_id > 0
    assert _count(db, "project") == before + 1
    names = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM project")}
    assert "Staged Project" in names


# --- exposure-template create under staged writes (regression: dxg) --------
# ts_assistant-dxg: POST /api/exposure-templates used to 400 with "attempt to write a
# readonly database" because the write hit the source in place. Staged writes (PR #34/#35)
# mean creates run on a local copy and publish by rename, so a read-only *file* no longer
# fails, and a read-only *directory* surfaces as DatabaseReadOnlyError (-> 409), never a
# bare 400/500.


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission checks")
def test_create_template_with_readonly_source_file_succeeds(tmp_path):
    """The original repro: the DB file itself is read-only (0o444). Because the write
    runs on a local working copy and publishes by atomic rename into the (writable)
    directory, the create SUCCEEDS instead of failing with SQLITE_READONLY."""
    db = _baseline(tmp_path / "t.sqlite")
    before = _count(db, "exposuretemplate")
    os.chmod(db, 0o444)  # read-only file: an in-place write would raise SQLITE_READONLY
    try:
        res = create_exposure_template(_new_template(), target_db=db, now=T0)
    finally:
        os.chmod(db, 0o644)
    assert res.template_id > 0
    assert _count(db, "exposuretemplate") == before + 1
    names = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM exposuretemplate")}
    assert "Staged Template" in names


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission checks")
def test_create_template_in_unwritable_dir_raises_readonly(tmp_path):
    """If the DB's folder isn't writable, publish can't rename in, so the create raises
    DatabaseReadOnlyError (which the templates router maps to 409) rather than a raw
    OSError / unhandled 400/500."""
    dbdir = tmp_path / "dbdir"
    dbdir.mkdir()
    db = _baseline(dbdir / "t.sqlite")
    before = _count(db, "exposuretemplate")
    os.chmod(dbdir, 0o555)  # readable (staging can read) but not writable
    try:
        with pytest.raises(DatabaseReadOnlyError):
            create_exposure_template(_new_template(), target_db=db, now=T0)
    finally:
        os.chmod(dbdir, 0o755)
    # The read-only environment left the source untouched.
    assert _count(db, "exposuretemplate") == before
