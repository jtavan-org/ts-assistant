"""Equipment store + API, focused on bg0 profile scoping."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import equipment
from app.main import app

# A complete rig body sans name (the API computes the FOV fields on the way out).
RIG = {
    "pixel_size_um": 3.76,
    "sensor_px_w": 6248,
    "sensor_px_h": 4176,
    "focal_length_mm": 530.0,
    "corrector_mag": 1.0,
}


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    # Empty store (no default seed) so each test controls exactly what's present.
    monkeypatch.setattr(equipment, "EQUIPMENT_FILE", tmp_path / "equipment.json")
    equipment._write([])


def test_create_carries_profile_id_and_filters():
    client = TestClient(app)
    client.post("/api/equipment", json={"name": "A-rig", "profile_id": "A", **RIG})
    client.post("/api/equipment", json={"name": "B-rig", "profile_id": "B", **RIG})

    a = client.get("/api/equipment?profile_id=A").json()
    assert {r["name"] for r in a} == {"A-rig"}
    assert a[0]["profile_id"] == "A"
    assert {r["name"] for r in client.get("/api/equipment?profile_id=B").json()} == {"B-rig"}
    # No filter returns both.
    assert len(client.get("/api/equipment").json()) == 2


def test_default_seed_visible_under_any_profile():
    """A rig with no profile_id (legacy/seed) shows under every profile."""
    equipment._write([equipment.EquipmentProfile(name="default", **RIG)])  # profile_id=None
    for pid in ("A", "B"):
        names = {r["name"] for r in TestClient(app).get(f"/api/equipment?profile_id={pid}").json()}
        assert "default" in names
