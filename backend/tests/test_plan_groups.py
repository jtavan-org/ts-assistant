"""Exposure plan group store + API (qiz.1 Stage 3, bead 759)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import plan_groups
from app.main import app


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_groups, "PLAN_GROUPS_FILE", tmp_path / "plan_groups.json")


def test_store_crud_roundtrip(tmp_path):
    assert plan_groups.list_groups() == []
    g = plan_groups.PlanGroup(
        name="LRGB Dark Nebula",
        items=[
            plan_groups.PlanGroupItem(exposure_template_id=1, desired=30),
            plan_groups.PlanGroupItem(exposure_template_id=2, desired=20),
        ],
    )
    saved = plan_groups.upsert(g)
    assert saved.id and len(saved.items) == 2
    # persisted + reloads
    again = plan_groups.list_groups()
    assert len(again) == 1 and again[0].name == "LRGB Dark Nebula"
    # update in place (same id, not appended)
    saved.name = "LRGB v2"
    plan_groups.upsert(saved)
    assert len(plan_groups.list_groups()) == 1
    assert plan_groups.list_groups()[0].name == "LRGB v2"
    # delete
    assert plan_groups.delete(saved.id) is True
    assert plan_groups.delete(saved.id) is False
    assert plan_groups.list_groups() == []


def test_api_crud():
    client = TestClient(app)
    assert client.get("/api/plan-groups").json() == []

    created = client.post(
        "/api/plan-groups",
        json={"name": "SHO Bright", "items": [{"exposure_template_id": 5, "desired": 40}]},
    ).json()
    gid = created["id"]
    assert gid and created["items"][0]["exposure_template_id"] == 5

    listed = client.get("/api/plan-groups").json()
    assert len(listed) == 1 and listed[0]["name"] == "SHO Bright"

    updated = client.put(
        f"/api/plan-groups/{gid}",
        json={"name": "SHO Bright v2", "items": [{"exposure_template_id": 5, "desired": 50}]},
    ).json()
    assert updated["id"] == gid and updated["name"] == "SHO Bright v2"
    assert updated["items"][0]["desired"] == 50

    assert client.delete(f"/api/plan-groups/{gid}").json() == {"ok": True}
    assert client.delete(f"/api/plan-groups/{gid}").status_code == 404
    assert client.get("/api/plan-groups").json() == []
