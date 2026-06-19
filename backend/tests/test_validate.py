"""Write validation against the target schema (bead mh3.3).

validate_schema runs pre-write inside the export transaction and rejects targets
whose schema can't safely accept our additive write, while tolerating the additive
column drift that real Target Scheduler version upgrades introduce.
"""

from __future__ import annotations

import pytest

from app.db.validate import ValidationError, validate_schema
from app.db.writer import (
    ExposurePlanSpec,
    ProjectSpec,
    TargetSpec,
    create_scheduler_db,
)

PROFILE = "11111111-1111-1111-1111-111111111111"


def _spec(profile_id: str = PROFILE) -> ProjectSpec:
    return ProjectSpec(
        profile_id=profile_id,
        name="P",
        targets=[
            TargetSpec(
                name="T",
                ra_deg=10.0,
                dec_deg=20.0,
                exposure_plans=[ExposurePlanSpec(filter_name="L", exposure=120.0)],
            )
        ],
    )


def _db(tmp_path):
    return create_scheduler_db(tmp_path / "c.sqlite")


def test_canonical_schema_validates(tmp_path):
    validate_schema(_db(tmp_path), _spec())  # no raise


def test_blank_profile_rejected(tmp_path):
    with pytest.raises(ValidationError, match="profile_id"):
        validate_schema(_db(tmp_path), _spec(profile_id="   "))


def test_missing_table_rejected(tmp_path):
    conn = _db(tmp_path)
    conn.execute("DROP TABLE exposureplan")
    conn.commit()
    with pytest.raises(ValidationError, match="missing tables"):
        validate_schema(conn, _spec())


def test_missing_written_column_rejected(tmp_path):
    conn = _db(tmp_path)
    conn.execute("ALTER TABLE target DROP COLUMN guid")  # a column the writer fills
    conn.commit()
    with pytest.raises(ValidationError, match="missing column"):
        validate_schema(conn, _spec())


def test_notnull_without_default_drift_rejected(tmp_path):
    conn = _db(tmp_path)
    # A newer schema that added a required column we don't know how to fill.
    conn.execute("ALTER TABLE project ADD COLUMN mandatory TEXT NOT NULL")
    conn.commit()
    with pytest.raises(ValidationError, match="NOT NULL"):
        validate_schema(conn, _spec())


def test_additive_drift_tolerated(tmp_path):
    conn = _db(tmp_path)
    # The shapes real TS upgrades introduce: nullable, or NOT NULL with a default.
    conn.execute("ALTER TABLE project ADD COLUMN extra TEXT")
    conn.execute("ALTER TABLE target ADD COLUMN extra2 INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    validate_schema(conn, _spec())  # no raise — extra columns are fine
