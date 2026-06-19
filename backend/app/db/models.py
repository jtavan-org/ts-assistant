"""Pydantic read-models returned by the API.

These are a clean, frontend-friendly projection of the Target Scheduler schema —
coordinates normalized to degrees, enums decoded to labels, exposure plans nested
under their target and targets nested under their project.
"""

from __future__ import annotations

from pydantic import BaseModel

# Target Scheduler enum decodings (integer code -> label). Unknown codes fall back
# to "state:<n>" etc. via the reader so we never lose information.
PROJECT_STATE = {0: "draft", 1: "active", 2: "inactive", 3: "closed"}
EPOCH_CODE = {0: "J2000", 1: "JNOW", 2: "B1950", 3: "J2050"}


class ExposurePlan(BaseModel):
    id: int
    filter_name: str | None = None  # resolved from the linked ExposureTemplate
    exposure: float | None = None
    desired: int = 0
    acquired: int = 0
    accepted: int = 0
    exposure_template_id: int | None = None


class Target(BaseModel):
    id: int
    name: str
    active: bool = True
    ra_deg: float
    dec_deg: float
    rotation: float = 0.0
    roi: float = 100.0
    epoch: str = "J2000"
    project_id: int
    project_name: str
    exposure_plans: list[ExposurePlan] = []


class Project(BaseModel):
    id: int
    name: str
    description: str | None = None
    profile_id: str | None = None
    state: str = "draft"
    priority: int | None = None
    is_mosaic: bool = False
    targets: list[Target] = []


class SchemaInfo(BaseModel):
    """Lightweight schema summary surfaced for diagnostics."""

    tables: dict[str, int]  # table name -> row count
    source_db: str | None
    db_present: bool
