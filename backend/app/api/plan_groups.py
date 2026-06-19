"""Exposure plan group CRUD endpoints (qiz.1 Stage 3).

Plan groups are app-local (no Target Scheduler table) — see :mod:`app.plan_groups`.
The frontend applies a group to a target, expanding it into exposure plans that the
normal /export path writes; nothing here touches the scheduler database.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from ..plan_groups import PlanGroup, delete, list_groups, upsert

router = APIRouter()


class PlanGroupInput(PlanGroup):
    """Body for create/update; id is optional (generated on create)."""


@router.get("/plan-groups", response_model=list[PlanGroup])
def get_plan_groups() -> list[PlanGroup]:
    return list_groups()


@router.post("/plan-groups", response_model=PlanGroup)
def create_plan_group(group: PlanGroupInput) -> PlanGroup:
    # Mint a fresh id on create (an empty client-sent id would persist id-less).
    group.id = uuid.uuid4().hex[:8]
    return upsert(group)


@router.put("/plan-groups/{group_id}", response_model=PlanGroup)
def update_plan_group(group_id: str, group: PlanGroupInput) -> PlanGroup:
    group.id = group_id
    return upsert(group)


@router.delete("/plan-groups/{group_id}")
def delete_plan_group(group_id: str) -> dict:
    if not delete(group_id):
        raise HTTPException(status_code=404, detail="plan group not found")
    return {"ok": True}
