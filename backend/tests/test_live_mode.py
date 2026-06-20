"""Live/production operating mode (o2c / bead j9t).

Proves the launch-time mode switch (TS_ASSISTANT_ALLOW_LIVE_WRITE) routes reads AND
writes at the real DB in LIVE mode, keeps STAGING behavior unchanged, applies the
live-DB safety treatment (back up before every write, WAL-aware change-detection,
integrity verified outside the write lock), and surfaces the mode via /api/health.
"""

from __future__ import annotations

from datetime import timedelta
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.config import build_mode
from app.db import backup, ops, reader, working_copy
from app.db import export as export_mod
from app.db.export import ExportError, export_project
from app.main import app
from app.db.writer import (
    ExposurePlanSpec,
    ProjectSpec,
    TargetSpec,
    create_scheduler_db,
    write_project,
)

PROFILE = "11111111-1111-1111-1111-111111111111"
T0 = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Redirect backups/ops/working/export to tmp; default to STAGING with no source.

    TS_ASSISTANT_DB defaults to a non-existent path so find_source_db() returns None
    and never globs the developer's real sample_database/ DB.
    """
    bdir = tmp_path / "backups"
    bdir.mkdir()
    monkeypatch.setattr(backup, "BACKUP_DIR", bdir)
    monkeypatch.setattr(ops, "OPS_FILE", tmp_path / "ops.json")
    monkeypatch.setattr(export_mod, "EXPORT_DB", tmp_path / "export.sqlite")
    monkeypatch.setattr(export_mod, "CONNECT_TIMEOUT", 0.5)
    monkeypatch.setattr(working_copy, "WORKING_DB", tmp_path / "working" / "schedulerdb.sqlite")
    (tmp_path / "working").mkdir()
    monkeypatch.delenv("TS_ASSISTANT_ALLOW_LIVE_WRITE", raising=False)
    monkeypatch.setenv("TS_ASSISTANT_DB", str(tmp_path / "no-such.sqlite"))


def _baseline(path: Path) -> Path:
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


def _new_project() -> ProjectSpec:
    return ProjectSpec(
        profile_id=PROFILE,
        name="NA Mosaic",
        targets=[
            TargetSpec(
                name="Panel 0",
                ra_deg=315.0,
                dec_deg=44.0,
                exposure_plans=[ExposurePlanSpec(filter_name="Ha", exposure=300.0, desired=30)],
            )
        ],
    )


def _count(db: Path, table: str) -> int:
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# --- mode resolution -------------------------------------------------------

def test_build_mode_staging_default():
    m = build_mode(live=False, source=Path("/x/db.sqlite"), integrity_env=False, staging_window=15)
    assert m.name == "STAGING"
    assert m.write_target == config.EXPORT_DB
    assert m.read_path == config.WORKING_DB
    assert m.backup_window_min == 15
    assert m.live_error is None


def test_build_mode_live():
    src = Path("/x/db.sqlite")
    m = build_mode(live=True, source=src)
    assert m.name == "LIVE"
    assert m.read_path == src and m.write_target == src
    assert m.integrity_check is True
    assert m.backup_window_min == 0
    assert m.live_error is None


def test_build_mode_live_without_source_sets_error():
    m = build_mode(live=True, source=None)
    assert m.name == "LIVE"
    assert m.write_target is None
    assert m.live_error and "no Target Scheduler database" in m.live_error


# --- live writes target the real DB and the reader sees them ---------------

def test_live_export_writes_source_and_reader_sees_it(tmp_path, monkeypatch):
    db = _baseline(tmp_path / "live.sqlite")
    monkeypatch.setenv("TS_ASSISTANT_DB", str(db))
    monkeypatch.setenv("TS_ASSISTANT_ALLOW_LIVE_WRITE", "1")
    before = _count(db, "project")

    res = export_project(_new_project(), now=T0)  # target_db=None -> the live source DB

    assert Path(res.target_db) == db.resolve()
    assert _count(db, "project") == before + 1  # written into the REAL db
    # A restore point was taken before the write.
    assert list(backup.BACKUP_DIR.glob("*.sqlite"))

    # The reader opens the live DB directly (no working-copy snapshot) and sees it.
    names = [p.name for p in reader.load_projects()]
    assert "NA Mosaic" in names
    assert not working_copy.WORKING_DB.exists()


def test_staging_leaves_source_untouched_and_snapshots(tmp_path, monkeypatch):
    db = _baseline(tmp_path / "src.sqlite")
    monkeypatch.setenv("TS_ASSISTANT_DB", str(db))  # source present, but live NOT enabled
    before = _count(db, "project")

    res = export_project(_new_project(), now=T0)  # -> staging EXPORT_DB

    assert Path(res.target_db) == (tmp_path / "export.sqlite").resolve()
    assert _count(db, "project") == before  # the real DB is untouched
    reader.load_projects()
    assert working_copy.WORKING_DB.exists()  # staging reads from a snapshot


def test_live_without_source_refuses_write(tmp_path, monkeypatch):
    # TS_ASSISTANT_DB points at a non-file -> find_source_db() is None.
    monkeypatch.setenv("TS_ASSISTANT_DB", str(tmp_path / "missing.sqlite"))
    monkeypatch.setenv("TS_ASSISTANT_ALLOW_LIVE_WRITE", "1")
    with pytest.raises(ExportError):
        export_project(_new_project(), now=T0)


# --- backup safety on WAL-style databases ----------------------------------

def test_backup_signature_includes_wal(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    sig0 = backup.backup_signature(db)
    (db.with_name(db.name + "-wal")).write_bytes(b"x" * 128)
    sig1 = backup.backup_signature(db)
    assert sig0 != sig1 and "wal:" in sig1


def test_live_backup_window_zero_never_coalesces(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    b1 = backup.ensure_backup(db, window_min=0, now=T0, live=True)
    b2 = backup.ensure_backup(db, window_min=0, now=T0 + timedelta(seconds=1), live=True)
    assert b2.path != b1.path  # every live write gets its own restore point


# --- health reports the active mode ----------------------------------------

def test_health_reports_live_mode(tmp_path, monkeypatch):
    db = _baseline(tmp_path / "live.sqlite")
    monkeypatch.setenv("TS_ASSISTANT_DB", str(db))
    monkeypatch.setenv("TS_ASSISTANT_ALLOW_LIVE_WRITE", "1")
    with TestClient(app) as client:  # `with` runs the lifespan probe
        h = client.get("/api/health").json()
    assert h["mode"] == "LIVE"
    assert h["write_target"] == str(db)
    assert h["live_error"] is None


def test_health_reports_staging_mode(tmp_path, monkeypatch):
    db = _baseline(tmp_path / "src.sqlite")
    monkeypatch.setenv("TS_ASSISTANT_DB", str(db))
    with TestClient(app) as client:
        h = client.get("/api/health").json()
    assert h["mode"] == "STAGING"
    assert h["live_error"] is None


def test_live_export_via_api_persists_on_reload(tmp_path, monkeypatch):
    """End-to-end through the real HTTP routes: POST /api/export in live mode writes
    the real DB, and GET /api/projects (a 'reload') reads it straight back."""
    db = _baseline(tmp_path / "live.sqlite")
    monkeypatch.setenv("TS_ASSISTANT_DB", str(db))
    monkeypatch.setenv("TS_ASSISTANT_ALLOW_LIVE_WRITE", "1")
    payload = {
        "profile_id": PROFILE,
        "name": "API Live Project",
        "is_mosaic": False,
        "targets": [
            {
                "name": "T1",
                "ra_deg": 10.0,
                "dec_deg": 20.0,
                "exposure_plans": [{"filter_name": "Ha", "exposure": 300.0, "desired": 5}],
            }
        ],
    }
    with TestClient(app) as client:
        res = client.post("/api/export", json=payload)
        assert res.status_code == 200, res.text
        assert Path(res.json()["target_db"]) == db.resolve()
        names = [p["name"] for p in client.get("/api/projects").json()]
        assert "API Live Project" in names  # persisted; not session-local in live mode
