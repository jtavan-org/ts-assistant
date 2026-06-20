"""Profile discovery + alias store + /api/profiles endpoint (bead bg0)."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app import profiles
from app.main import app
from tests.make_fixture import PROFILE, build


@pytest.fixture(autouse=True)
def isolate_aliases(tmp_path, monkeypatch):
    """Keep the alias store out of the repo's data dir."""
    monkeypatch.setattr(profiles, "PROFILES_FILE", tmp_path / "profiles.json")


@pytest.fixture()
def reader(tmp_path, monkeypatch):
    """Point the app at a fresh fixture DB (mirrors test_reader.reader)."""
    src = tmp_path / "schedulerdb.sqlite"
    build(src)
    monkeypatch.setenv("TS_ASSISTANT_DB", str(src))

    from app import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "WORKING_DIR", tmp_path / "data" / "working")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "data" / "backups")

    from app.db import working_copy

    monkeypatch.setattr(
        working_copy, "WORKING_DB", tmp_path / "data" / "working" / "schedulerdb.sqlite"
    )

    from app.db import reader as reader_mod

    importlib.reload(reader_mod)
    return reader_mod


def test_load_profile_ids(reader):
    # The fixture stamps a single profile across projects, templates, and plans.
    assert reader.load_profile_ids() == [PROFILE]


def test_alias_store_roundtrip():
    assert profiles.get_aliases() == {}
    profiles.set_alias("abc", "Main rig")
    assert profiles.get_aliases() == {"abc": "Main rig"}
    # upsert overwrites in place, not append
    profiles.set_alias("abc", "Backyard")
    assert profiles.get_aliases() == {"abc": "Backyard"}


def test_profiles_endpoint_falls_back_to_truncated_guid(reader):
    client = TestClient(app)
    body = client.get("/api/profiles").json()
    assert body == [{"id": PROFILE, "name": PROFILE[:8]}]


def test_profiles_endpoint_reflects_alias(reader):
    client = TestClient(app)
    put = client.put(f"/api/profiles/{PROFILE}", json={"name": "Main rig"})
    assert put.status_code == 200
    assert put.json() == {"id": PROFILE, "name": "Main rig"}

    body = client.get("/api/profiles").json()
    assert body == [{"id": PROFILE, "name": "Main rig"}]
