"""Exposure plan template store + API (qiz.1 Stage 3, bead 759)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import plan_templates
from app.main import app


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_templates, "PLAN_TEMPLATES_FILE", tmp_path / "plan_templates.json")
    monkeypatch.setattr(plan_templates, "LEGACY_FILE", tmp_path / "plan_groups.json")


def test_store_crud_roundtrip(tmp_path):
    assert plan_templates.list_plan_templates() == []
    g = plan_templates.PlanTemplate(
        name="LRGB Dark Nebula",
        items=[
            plan_templates.PlanTemplateItem(exposure_template_id=1, desired=30),
            plan_templates.PlanTemplateItem(exposure_template_id=2, desired=20),
        ],
    )
    saved = plan_templates.upsert(g)
    assert saved.id and len(saved.items) == 2
    # persisted + reloads
    again = plan_templates.list_plan_templates()
    assert len(again) == 1 and again[0].name == "LRGB Dark Nebula"
    # update in place (same id, not appended)
    saved.name = "LRGB v2"
    plan_templates.upsert(saved)
    assert len(plan_templates.list_plan_templates()) == 1
    assert plan_templates.list_plan_templates()[0].name == "LRGB v2"
    # delete
    assert plan_templates.delete(saved.id) is True
    assert plan_templates.delete(saved.id) is False
    assert plan_templates.list_plan_templates() == []


def test_legacy_migration(tmp_path):
    """A pre-rename plan_groups.json is migrated forward (and self-cleans) so saved
    definitions survive the rename — they persist across launches."""
    import json

    plan_templates.LEGACY_FILE.write_text(
        json.dumps([{"id": "abc", "name": "LRGBSHO Nebula", "items": [
            {"exposure_template_id": 1, "desired": 40},
        ]}])
    )
    loaded = plan_templates.list_plan_templates()
    assert [t.name for t in loaded] == ["LRGBSHO Nebula"]
    assert loaded[0].items[0].desired == 40
    # new file written, legacy renamed so it never re-runs
    assert plan_templates.PLAN_TEMPLATES_FILE.is_file()
    assert not plan_templates.LEGACY_FILE.is_file()
    assert (tmp_path / "plan_groups.json.migrated").is_file()
    # idempotent + persists on a second read
    assert [t.name for t in plan_templates.list_plan_templates()] == ["LRGBSHO Nebula"]


def test_legacy_migration_merges_current_wins(tmp_path):
    """If both files exist, current entries override legacy on id collision."""
    import json

    plan_templates.LEGACY_FILE.write_text(
        json.dumps([{"id": "x", "name": "old", "items": []}])
    )
    plan_templates.PLAN_TEMPLATES_FILE.write_text(
        json.dumps([{"id": "x", "name": "new", "items": []}])
    )
    loaded = plan_templates.list_plan_templates()
    assert [t.name for t in loaded] == ["new"]


def test_api_crud():
    client = TestClient(app)
    assert client.get("/api/plan-templates").json() == []

    created = client.post(
        "/api/plan-templates",
        json={"name": "SHO Bright", "items": [{"exposure_template_id": 5, "desired": 40}]},
    ).json()
    gid = created["id"]
    assert gid and created["items"][0]["exposure_template_id"] == 5

    listed = client.get("/api/plan-templates").json()
    assert len(listed) == 1 and listed[0]["name"] == "SHO Bright"

    updated = client.put(
        f"/api/plan-templates/{gid}",
        json={"name": "SHO Bright v2", "items": [{"exposure_template_id": 5, "desired": 50}]},
    ).json()
    assert updated["id"] == gid and updated["name"] == "SHO Bright v2"
    assert updated["items"][0]["desired"] == 50

    assert client.delete(f"/api/plan-templates/{gid}").json() == {"ok": True}
    assert client.delete(f"/api/plan-templates/{gid}").status_code == 404
    assert client.get("/api/plan-templates").json() == []
