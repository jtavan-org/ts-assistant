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


class OverrideStep(BaseModel):
    """One step of a target's override exposure order (awh). action 0 = expose the plan
    at reference_idx; action 1 = a Dither step (reference_idx = -1)."""

    order: int
    action: int
    reference_idx: int


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
    override_exposure_order: list[OverrideStep] = []


class RuleWeight(BaseModel):
    """One scoring-rule weight for a project (used by the planner)."""

    name: str
    weight: float


class Project(BaseModel):
    id: int
    name: str
    description: str | None = None
    profile_id: str | None = None
    state: str = "draft"
    priority: int | None = None
    is_mosaic: bool = False
    targets: list[Target] = []
    rule_weights: list[RuleWeight] = []
    # Advanced project settings (psq) — surfaced so the editor can prefill them.
    minimum_time: int | None = None
    minimum_altitude: float | None = None
    maximum_altitude: float | None = None
    use_custom_horizon: bool | None = None
    horizon_offset: float | None = None
    meridian_window: int | None = None
    filter_switch_frequency: int | None = None
    dither_every: int | None = None
    enable_grader: bool | None = None
    flats_handling: int | None = None
    smart_exposure_order: bool | None = None


class ExposureTemplate(BaseModel):
    """A Target Scheduler exposure template (capture definition for one filter).

    Surfaced so exposure plans can reference the user's real, fully-configured
    templates (gain/offset/bin/moon-avoidance) instead of an auto-created bare one.
    Columns beyond Id/name/filter are tolerant of schema drift (None when absent).
    """

    id: int
    profile_id: str | None = None
    name: str
    filter_name: str | None = None
    gain: int | None = None
    offset: int | None = None
    binning: int | None = None
    readout_mode: int | None = None
    twilight_level: int | None = None
    moon_avoidance_enabled: bool | None = None
    moon_avoidance_separation: float | None = None
    moon_avoidance_width: int | None = None
    maximum_humidity: float | None = None
    default_exposure: float | None = None
    dither_every: int | None = None
    minutes_offset: int | None = None


class SchemaInfo(BaseModel):
    """Lightweight schema summary surfaced for diagnostics."""

    tables: dict[str, int]  # table name -> row count
    source_db: str | None
    db_present: bool
