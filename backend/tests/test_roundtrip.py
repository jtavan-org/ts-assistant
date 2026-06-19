"""Schema round-trip fidelity test (bead mh3.1) — gates the P3 writer.

Proves that writing a Project + Targets + ExposurePlans with the faithful writer
produces a database that (a) is schema-identical to a NINA-written one and (b)
reads back through the existing projection with no data loss. This is the
contract the real transactional writer/exporter (mh3.2/mh3.4) must satisfy.

The bead names "__EFMigrationsHistory expectations": empirically the Target
Scheduler DB has NO such table (it uses hand-written SQL migrations, not EF Core),
so the expectation we pin is that the table must NOT exist — see
test_no_ef_or_sequence_metadata.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db import schema
from app.db.reader import load_projects_conn
from app.db.writer import (
    ExposurePlanSpec,
    ProjectSpec,
    TargetSpec,
    create_scheduler_db,
    write_project,
)

REAL_DB = Path(__file__).resolve().parents[2] / "sample_database" / "schedulerdb.sqlite"
PROFILE = "11111111-1111-1111-1111-111111111111"
FLOAT_TOL = 1e-9


def _sho_plans() -> list[ExposurePlanSpec]:
    return [
        ExposurePlanSpec(filter_name=f, exposure=300.0, desired=30, acquired=12, accepted=10)
        for f in ("Ha", "OIII", "SII")
    ]


def mosaic_spec() -> ProjectSpec:
    """A 2x2 SHO mosaic of the North America / Pelican region (RA in degrees)."""
    panels = [
        ("Panel 1-1", 312.0, 45.0),
        ("Panel 1-2", 315.0, 45.0),
        ("Panel 2-1", 312.0, 43.0),
        ("Panel 2-2", 315.0, 43.0),
    ]
    return ProjectSpec(
        profile_id=PROFILE,
        name="NA/Pelican Mosaic",
        description="2x2 SHO mosaic",
        state=1,  # active
        priority=2,
        is_mosaic=True,
        targets=[
            TargetSpec(name=n, ra_deg=ra, dec_deg=dec, rotation=10.0, exposure_plans=_sho_plans())
            for (n, ra, dec) in panels
        ],
    )


def single_spec() -> ProjectSpec:
    return ProjectSpec(
        profile_id=PROFILE,
        name="M31 Andromeda",
        state=0,  # draft
        priority=1,
        is_mosaic=False,
        targets=[
            TargetSpec(
                name="M31",
                ra_deg=10.6847,
                dec_deg=41.269,
                rotation=35.0,
                exposure_plans=[ExposurePlanSpec(filter_name="Ha", exposure=180.0, desired=60)],
            )
        ],
    )


@pytest.fixture()
def written_db(tmp_path) -> sqlite3.Connection:
    """A fresh scheduler DB with the two sample projects written into it."""
    conn = create_scheduler_db(tmp_path / "out.sqlite")
    write_project(conn, mosaic_spec())
    write_project(conn, single_spec())
    conn.commit()
    return conn


# --- schema fidelity -------------------------------------------------------

def _tables(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        n: s for n, s in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table'"
        )
    }


def test_fresh_db_schema_is_canonical(tmp_path):
    conn = create_scheduler_db(tmp_path / "fresh.sqlite")
    got = _tables(conn)
    canonical = schema.canonical_sql_by_table()
    assert set(got) == set(canonical) == set(schema.EXPECTED_TABLES)
    # Every CREATE statement must be byte-identical to the canonical one.
    for name in canonical:
        assert got[name] == canonical[name], f"DDL drift in table {name}"


@pytest.mark.skipif(not REAL_DB.exists(), reason="real schedulerdb.sqlite not present")
def test_fresh_db_matches_real_nina_schema(tmp_path):
    conn = create_scheduler_db(tmp_path / "fresh.sqlite")
    got = _tables(conn)
    real = _tables(sqlite3.connect(REAL_DB))
    assert set(got) == set(real), "table set differs from real NINA DB"
    for name in real:
        assert got[name] == real[name], f"DDL differs from NINA for table {name}"


def test_no_ef_or_sequence_metadata(tmp_path):
    conn = create_scheduler_db(tmp_path / "fresh.sqlite")
    names = {
        n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "__EFMigrationsHistory" not in names  # not EF Core
    assert "sqlite_sequence" not in names  # no AUTOINCREMENT
    assert names == set(schema.EXPECTED_TABLES)


# --- data round-trip fidelity ---------------------------------------------

def test_roundtrip_project_fidelity(written_db):
    projects = {p.name: p for p in load_projects_conn(written_db)}
    assert set(projects) == {"NA/Pelican Mosaic", "M31 Andromeda"}

    mosaic = projects["NA/Pelican Mosaic"]
    assert mosaic.is_mosaic is True
    assert mosaic.state == "active"
    assert mosaic.priority == 2
    assert mosaic.profile_id == PROFILE
    assert len(mosaic.targets) == 4

    spec_by_name = {t.name: t for t in mosaic_spec().targets}
    for t in mosaic.targets:
        s = spec_by_name[t.name]
        assert t.ra_deg == pytest.approx(s.ra_deg, abs=1e-6)
        assert t.dec_deg == pytest.approx(s.dec_deg, abs=1e-9)
        assert t.rotation == pytest.approx(10.0)
        assert t.roi == pytest.approx(100.0)
        assert t.epoch == "J2000"
        assert t.active is True
        assert {p.filter_name for p in t.exposure_plans} == {"Ha", "OIII", "SII"}
        for p in t.exposure_plans:
            assert p.exposure == pytest.approx(300.0)
            assert (p.desired, p.acquired, p.accepted) == (30, 12, 10)

    single = projects["M31 Andromeda"]
    assert single.is_mosaic is False
    assert single.state == "draft"
    assert len(single.targets) == 1
    (m31,) = single.targets
    assert m31.ra_deg == pytest.approx(10.6847, abs=1e-6)
    assert m31.rotation == pytest.approx(35.0)
    assert [p.filter_name for p in m31.exposure_plans] == ["Ha"]
    assert m31.exposure_plans[0].desired == 60


def test_ra_persisted_in_hours(written_db):
    # The on-disk RA column must be in HOURS (ra_deg / 15) — the inverse of the
    # reader's ×15. Pick Panel 1-2 (ra_deg = 315 -> 21.0 h).
    (ra_hours,) = written_db.execute(
        "SELECT ra FROM target WHERE name = ?", ("Panel 1-2",)
    ).fetchone()
    assert ra_hours == pytest.approx(315.0 / 15.0, abs=FLOAT_TOL)


def test_foreign_key_integrity(written_db):
    violations = list(written_db.execute("PRAGMA foreign_key_check"))
    assert violations == [], f"FK violations: {violations}"
    # Every exposure plan points at a real target and a real template.
    orphans = written_db.execute(
        "SELECT COUNT(*) FROM exposureplan ep"
        " LEFT JOIN target t ON t.Id = ep.targetid"
        " LEFT JOIN exposuretemplate et ON et.Id = ep.exposureTemplateId"
        " WHERE t.Id IS NULL OR et.Id IS NULL"
    ).fetchone()[0]
    assert orphans == 0


def test_ids_assigned_and_guids_present(written_db):
    # Rowid-alias Ids are assigned and unique; every project/target row has a guid.
    pids = [r[0] for r in written_db.execute("SELECT Id FROM project")]
    assert pids == sorted(set(pids)) and all(isinstance(i, int) for i in pids)
    missing_guid = written_db.execute(
        "SELECT COUNT(*) FROM target WHERE guid IS NULL OR guid = ''"
    ).fetchone()[0]
    assert missing_guid == 0
