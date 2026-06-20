"""Exposure plan templates (app-local, persisted to data/plan_templates.json).

An *exposure plan template* is a named, reusable bundle of ``{exposure_template_id,
desired}`` items — e.g. "LRGB Dark Nebula" or "SHO Bright Target". This is a TS
Assistant authoring convenience with **no Target Scheduler table**: applying one to a
target just expands it into ordinary exposure plans (each referencing its exposure
template) which the existing write path persists. Stored outside the Target Scheduler
database.
"""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, Field

from .config import DATA_DIR, ensure_dirs

PLAN_TEMPLATES_FILE = DATA_DIR / "plan_templates.json"


class PlanTemplateItem(BaseModel):
    """One filter in a plan template: an exposure template + how many frames to collect."""

    exposure_template_id: int
    desired: int = 1


class PlanTemplate(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    # NINA profile this template belongs to (bg0). None = legacy/unscoped, shown under
    # every profile until the user edits it (which stamps the active profile).
    profile_id: str | None = None
    items: list[PlanTemplateItem] = Field(default_factory=list)


def _read() -> list[PlanTemplate]:
    ensure_dirs()
    if not PLAN_TEMPLATES_FILE.is_file():
        return []
    raw = json.loads(PLAN_TEMPLATES_FILE.read_text() or "[]")
    return [PlanTemplate(**r) for r in raw]


def _write(items: list[PlanTemplate]) -> None:
    ensure_dirs()
    PLAN_TEMPLATES_FILE.write_text(json.dumps([t.model_dump() for t in items], indent=2))


def list_plan_templates(profile_id: str | None = None) -> list[PlanTemplate]:
    # A None entry (legacy/unscoped) is visible everywhere; otherwise ids must match.
    return [
        t
        for t in _read()
        if profile_id is None or t.profile_id is None or t.profile_id == profile_id
    ]


def upsert(pt: PlanTemplate) -> PlanTemplate:
    items = _read()
    for i, existing in enumerate(items):
        if existing.id == pt.id:
            items[i] = pt
            break
    else:
        items.append(pt)
    _write(items)
    return pt


def delete(pt_id: str) -> bool:
    items = _read()
    kept = [t for t in items if t.id != pt_id]
    if len(kept) == len(items):
        return False
    _write(kept)
    return True
