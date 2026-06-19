"""Exposure plan groups (app-local, persisted to data/plan_groups.json).

A *plan group* is a named, reusable bundle of ``{exposure_template_id, desired}``
items — e.g. "LRGB Dark Nebula" or "SHO Bright Target". This is a TS Assistant
authoring convenience with **no Target Scheduler table**: applying a group to a
target just expands it into ordinary exposure plans (each referencing its template)
which the existing write path persists. Stored outside the Target Scheduler database.
"""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, Field

from .config import DATA_DIR, ensure_dirs

PLAN_GROUPS_FILE = DATA_DIR / "plan_groups.json"


class PlanGroupItem(BaseModel):
    """One filter in a group: an exposure template + how many frames to collect."""

    exposure_template_id: int
    desired: int = 1


class PlanGroup(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    items: list[PlanGroupItem] = Field(default_factory=list)


def _read() -> list[PlanGroup]:
    ensure_dirs()
    if not PLAN_GROUPS_FILE.is_file():
        return []
    raw = json.loads(PLAN_GROUPS_FILE.read_text() or "[]")
    return [PlanGroup(**r) for r in raw]


def _write(groups: list[PlanGroup]) -> None:
    ensure_dirs()
    PLAN_GROUPS_FILE.write_text(json.dumps([g.model_dump() for g in groups], indent=2))


def list_groups() -> list[PlanGroup]:
    return _read()


def upsert(group: PlanGroup) -> PlanGroup:
    groups = _read()
    for i, g in enumerate(groups):
        if g.id == group.id:
            groups[i] = group
            break
    else:
        groups.append(group)
    _write(groups)
    return group


def delete(group_id: str) -> bool:
    groups = _read()
    kept = [g for g in groups if g.id != group_id]
    if len(kept) == len(groups):
        return False
    _write(kept)
    return True
