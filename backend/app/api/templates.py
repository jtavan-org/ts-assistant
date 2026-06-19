"""Exposure-template endpoints.

Stage 1 (qiz.1) serves the user's existing Target Scheduler exposure templates so
the Project builder can reference a real, fully-configured template by Id instead of
auto-creating a bare one. Creating new templates (POST) is Stage 2 (qiz.5).
"""

from __future__ import annotations

from fastapi import APIRouter

from ..db.models import ExposureTemplate
from ..db.reader import load_exposure_templates

router = APIRouter()


@router.get("/exposure-templates", response_model=list[ExposureTemplate])
def get_exposure_templates() -> list[ExposureTemplate]:
    return load_exposure_templates()
