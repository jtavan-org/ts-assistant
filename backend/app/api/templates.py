"""Exposure-template endpoints.

GET (qiz.1) serves the user's existing Target Scheduler exposure templates so the
Project builder can reference a real template by Id. POST (qiz.5) creates a new
template additively (one row, backed-up + provenanced + undoable) via the export
orchestrator. Like project saves, the write runs on a local working copy and is
published back to the configured database via an atomic compare-and-swap (staged
writes), taking a backup first. Editing/deleting existing templates is out of scope
(that's a modify op).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db.export import (
    DatabaseBusyError,
    DatabaseReadOnlyError,
    ExportError,
    ProgressError,
    create_exposure_template,
)
from ..db.models import ExposureTemplate
from ..db.reader import load_exposure_templates
from ..db.validate import ValidationError
from ..db.writer import ExposureTemplateSpec

router = APIRouter()


@router.get("/exposure-templates", response_model=list[ExposureTemplate])
def get_exposure_templates() -> list[ExposureTemplate]:
    return load_exposure_templates()


@router.post("/exposure-templates", response_model=ExposureTemplate)
def create_exposure_template_endpoint(spec: ExposureTemplateSpec) -> ExposureTemplate:
    """Additively create one exposure template; returns it with its new Id."""
    try:
        res = create_exposure_template(spec)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except (DatabaseBusyError, DatabaseReadOnlyError, ProgressError) as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ExportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ExposureTemplate(
        id=res.template_id,
        profile_id=spec.profile_id,
        name=spec.name,
        filter_name=spec.filter_name,
        gain=spec.gain,
        offset=spec.offset,
        binning=spec.binning,
        readout_mode=spec.readout_mode,
        twilight_level=spec.twilight_level,
        moon_avoidance_enabled=spec.moon_avoidance_enabled,
        moon_avoidance_separation=spec.moon_avoidance_separation,
        moon_avoidance_width=spec.moon_avoidance_width,
        maximum_humidity=spec.maximum_humidity,
        default_exposure=spec.default_exposure,
        dither_every=spec.dither_every,
        minutes_offset=spec.minutes_offset,
    )
