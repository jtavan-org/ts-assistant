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
# Pre-rename filename (these were once called "plan groups"). We migrate it forward
# one time so the rename never loses a user's saved definitions.
LEGACY_FILE = DATA_DIR / "plan_groups.json"


class PlanTemplateItem(BaseModel):
    """One filter in a plan template: an exposure template + how many frames to collect."""

    exposure_template_id: int
    desired: int = 1


class PlanTemplate(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    items: list[PlanTemplateItem] = Field(default_factory=list)


def _load_raw(path) -> list[dict]:
    try:
        return json.loads(path.read_text() or "[]")
    except (OSError, json.JSONDecodeError):
        return []


def _migrate_legacy() -> None:
    """One-time forward-migration from the pre-rename ``plan_groups.json``.

    Merges any legacy definitions into the current file (current wins on id
    collision, so post-rename edits aren't clobbered) and renames the legacy file
    so this never runs again.
    """
    if not LEGACY_FILE.is_file():
        return
    by_id: dict[str, dict] = {}
    for r in _load_raw(LEGACY_FILE) + _load_raw(PLAN_TEMPLATES_FILE):
        if isinstance(r, dict) and "id" in r:
            by_id[r["id"]] = r  # later (current) entries override earlier (legacy)
    PLAN_TEMPLATES_FILE.write_text(json.dumps(list(by_id.values()), indent=2))
    LEGACY_FILE.rename(LEGACY_FILE.parent / (LEGACY_FILE.name + ".migrated"))


def _read() -> list[PlanTemplate]:
    ensure_dirs()
    _migrate_legacy()
    if not PLAN_TEMPLATES_FILE.is_file():
        return []
    return [PlanTemplate(**r) for r in _load_raw(PLAN_TEMPLATES_FILE)]


def _write(items: list[PlanTemplate]) -> None:
    ensure_dirs()
    PLAN_TEMPLATES_FILE.write_text(json.dumps([t.model_dump() for t in items], indent=2))


def list_plan_templates() -> list[PlanTemplate]:
    return _read()


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
