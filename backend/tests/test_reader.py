"""Tests for the read layer against a schema-faithful fixture database."""

from __future__ import annotations

import importlib

import pytest

from tests.make_fixture import build


@pytest.fixture()
def reader(tmp_path, monkeypatch):
    """Point the app at a fresh fixture DB (read in place) and reload the reader."""
    src = tmp_path / "schedulerdb.sqlite"
    build(src)
    monkeypatch.setenv("TS_ASSISTANT_DB", str(src))

    from app import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "data" / "backups")

    from app.db import reader as reader_mod

    importlib.reload(reader_mod)
    return reader_mod


def test_loads_projects_and_targets(reader):
    projects = reader.load_projects()
    assert len(projects) == 2

    by_name = {p.name: p for p in projects}
    mosaic = by_name["NA/Pelican Mosaic"]
    assert mosaic.is_mosaic is True
    assert mosaic.state == "active"
    assert len(mosaic.targets) == 4

    single = by_name["M31 Andromeda"]
    assert single.state == "draft"
    assert len(single.targets) == 1


def test_coordinates_converted_hours_to_degrees(reader):
    targets = {t.name: t for t in reader.load_targets()}
    # M31 stored as 0.712313 h -> 10.6847 deg; Dec stays in degrees.
    m31 = targets["M31"]
    assert m31.ra_deg == pytest.approx(10.6847, abs=1e-3)
    assert m31.dec_deg == pytest.approx(41.269, abs=1e-3)
    assert m31.rotation == pytest.approx(35.0)
    # Panel stored as 20.8 h -> 312.0 deg.
    panel = targets["Panel 1-1"]
    assert panel.ra_deg == pytest.approx(312.0, abs=1e-6)
    assert panel.dec_deg == pytest.approx(45.0, abs=1e-6)


def test_exposure_plans_with_filter_names(reader):
    targets = {t.name: t for t in reader.load_targets()}
    panel = targets["Panel 1-1"]
    filters = sorted(p.filter_name for p in panel.exposure_plans)
    assert filters == ["Ha", "OIII", "SII"]
    ha = next(p for p in panel.exposure_plans if p.filter_name == "Ha")
    assert ha.desired == 30
    assert ha.accepted == 10


def test_loads_exposure_templates(reader):
    templates = reader.load_exposure_templates()
    by_filter = {t.filter_name: t for t in templates}
    assert sorted(by_filter) == ["Ha", "OIII", "SII"]
    ha = by_filter["Ha"]
    assert ha.name == "Ha"
    assert ha.default_exposure == pytest.approx(300.0)
    assert ha.id > 0
    # full columns project through with the fixture's defaults
    assert ha.gain == -1
    assert ha.moon_avoidance_enabled is False
    assert ha.moon_avoidance_separation == pytest.approx(0.0)


def test_loads_rule_weights(reader):
    from tests.make_fixture import FIXTURE_RULE_WEIGHTS

    projects = {p.name: p for p in reader.load_projects()}
    rw = {w.name: w.weight for w in projects["M31 Andromeda"].rule_weights}
    assert rw == {name: weight for name, weight in FIXTURE_RULE_WEIGHTS}
    assert len(rw) == 8


def test_loads_project_settings(reader):
    projects = {p.name: p for p in reader.load_projects()}
    m31 = projects["M31 Andromeda"]
    assert m31.minimum_time == 45  # seeded non-default
    assert m31.enable_grader is False  # seeded 0
    # a default-valued setting still projects through
    assert m31.minimum_altitude == 0.0


def test_schema_info(reader):
    info = reader.schema_info()
    assert info.db_present is True
    assert info.tables["target"] == 5  # 4 mosaic panels + M31
    assert info.tables["project"] == 2
