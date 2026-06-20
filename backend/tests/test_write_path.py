"""Single in-place write path (bead ur0).

The app reads and writes the one configured Target Scheduler DB in place, taking a
consistent backup before every write. These tests prove: writes land in the real DB
and the reader sees them on reload; a missing DB is refused cleanly; a fresh backup is
taken before each write; the backup signature notices WAL changes; and /api/health
reports the database in use.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import backup, ops, reader
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
    """Redirect backups/ops to tmp; default to 'no database configured'.

    TS_ASSISTANT_DB defaults to a non-existent path so find_source_db() returns None
    and never globs the developer's real sample_database/ DB.
    """
    bdir = tmp_path / "backups"
    bdir.mkdir()
    monkeypatch.setattr(backup, "BACKUP_DIR", bdir)
    monkeypatch.setattr(ops, "OPS_FILE", tmp_path / "ops.json")
    monkeypatch.setattr(export_mod, "CONNECT_TIMEOUT", 0.5)
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


def test_export_writes_in_place_and_reader_sees_it(tmp_path, monkeypatch):
    db = _baseline(tmp_path / "scheduler.sqlite")
    monkeypatch.setenv("TS_ASSISTANT_DB", str(db))
    before = _count(db, "project")

    res = export_project(_new_project(), now=T0)  # target_db=None -> the configured DB

    assert Path(res.target_db) == db.resolve()
    assert _count(db, "project") == before + 1  # written into the real DB in place
    assert list(backup.BACKUP_DIR.glob("*.sqlite"))  # a restore point was taken first

    # The reader opens the same DB directly (no snapshot) and sees the new project.
    names = [p.name for p in reader.load_projects()]
    assert "NA Mosaic" in names


def test_export_without_database_refuses(tmp_path, monkeypatch):
    # TS_ASSISTANT_DB points at a non-file -> find_source_db() is None.
    monkeypatch.setenv("TS_ASSISTANT_DB", str(tmp_path / "missing.sqlite"))
    with pytest.raises(ExportError):
        export_project(_new_project(), now=T0)


def test_backup_before_every_write(tmp_path, monkeypatch):
    db = _baseline(tmp_path / "scheduler.sqlite")
    monkeypatch.setenv("TS_ASSISTANT_DB", str(db))
    b1 = backup.ensure_backup(db, now=T0)
    b2 = backup.ensure_backup(db, now=T0 + timedelta(seconds=1))
    assert b2.path != b1.path  # every write gets its own restore point (no coalescing)


def test_backup_signature_includes_wal(tmp_path):
    db = _baseline(tmp_path / "t.sqlite")
    sig0 = backup.backup_signature(db)
    (db.with_name(db.name + "-wal")).write_bytes(b"x" * 128)
    sig1 = backup.backup_signature(db)
    assert sig0 != sig1 and "wal:" in sig1


def test_health_reports_database(tmp_path, monkeypatch):
    db = _baseline(tmp_path / "scheduler.sqlite")
    monkeypatch.setenv("TS_ASSISTANT_DB", str(db))
    with TestClient(app) as client:
        h = client.get("/api/health").json()
    assert h["db_present"] is True
    assert h["db_path"] == str(db)
    assert h["error"] is None


def test_health_no_database(tmp_path, monkeypatch):
    monkeypatch.setenv("TS_ASSISTANT_DB", str(tmp_path / "missing.sqlite"))
    with TestClient(app) as client:
        h = client.get("/api/health").json()
    assert h["db_present"] is False
    assert h["db_path"] is None


def test_export_via_api_persists_on_reload(tmp_path, monkeypatch):
    """End-to-end through the real HTTP routes: POST /api/export writes the DB in place,
    and GET /api/projects (a 'reload') reads it straight back."""
    db = _baseline(tmp_path / "scheduler.sqlite")
    monkeypatch.setenv("TS_ASSISTANT_DB", str(db))
    payload = {
        "profile_id": PROFILE,
        "name": "API Project",
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
        assert "API Project" in names  # persisted; reads come straight from the DB
