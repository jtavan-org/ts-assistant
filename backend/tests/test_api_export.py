"""HTTP tests for the export API (bead mh3.4)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import backup, export, ops
from app.db.writer import create_scheduler_db
from app.main import app

PROFILE = "11111111-1111-1111-1111-111111111111"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    bdir = tmp_path / "backups"
    bdir.mkdir()
    db = tmp_path / "scheduler.sqlite"
    create_scheduler_db(db).close()  # a valid empty canonical DB to write in place
    monkeypatch.setattr(backup, "BACKUP_DIR", bdir)
    monkeypatch.setattr(ops, "OPS_FILE", tmp_path / "ops.json")
    monkeypatch.setattr(export, "find_source_db", lambda: db)
    return TestClient(app)


def _payload():
    return {
        "profile_id": PROFILE,
        "name": "API Mosaic",
        "is_mosaic": True,
        "targets": [
            {
                "name": "Panel 1",
                "ra_deg": 315.0,
                "dec_deg": 44.0,
                "rotation": 10.0,
                "exposure_plans": [
                    {"filter_name": "Ha", "exposure": 300.0, "desired": 30},
                    {"filter_name": "OIII", "exposure": 300.0, "desired": 30},
                ],
            }
        ],
    }


def test_export_then_undo(client):
    r = client.post("/api/export", json=_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project_id"] > 0
    assert body["counts"] == {
        "project": 1, "target": 1, "exposureplan": 2, "exposuretemplate": 2, "ruleweight": 8,
    }
    assert body["backup_path"]
    assert body["target_db"].endswith("scheduler.sqlite")

    op = body["operation_id"]
    u = client.post(f"/api/export/{op}/undo")
    assert u.status_code == 200, u.text
    assert u.json()["deleted"]["project"] == 1


def test_export_then_delete(client):
    created = client.post("/api/export", json=_payload()).json()
    pid = created["project_id"]
    r = client.delete(f"/api/export/{pid}")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"]["project"] == 1
    # gone from the project list
    names = [p["name"] for p in client.get("/api/projects").json()]
    assert "API Mosaic" not in names


def test_export_rejects_blank_profile(client):
    bad = _payload()
    bad["profile_id"] = "   "
    r = client.post("/api/export", json=bad)
    assert r.status_code == 422
    assert "profile_id" in r.json()["detail"]


def test_undo_unknown_operation_400(client):
    r = client.post("/api/export/op_does_not_exist/undo")
    assert r.status_code == 400


def test_create_exposure_template(client):
    r = client.post(
        "/api/exposure-templates",
        json={
            "profile_id": PROFILE,
            "name": "Ha 900s",
            "filter_name": "Ha",
            "gain": 120,
            "offset": 30,
            "default_exposure": 900.0,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] > 0
    assert body["name"] == "Ha 900s" and body["filter_name"] == "Ha"
    assert body["gain"] == 120 and body["default_exposure"] == 900.0


def test_create_exposure_template_rejects_blank_name(client):
    r = client.post(
        "/api/exposure-templates",
        json={"profile_id": PROFILE, "name": "  ", "filter_name": "Ha"},
    )
    assert r.status_code == 422
    assert "name" in r.json()["detail"]
