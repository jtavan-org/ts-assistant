"""Exposure plan template CRUD endpoints (qiz.1 Stage 3).

Exposure plan templates are app-local (no Target Scheduler table) — see
:mod:`app.plan_templates`. The frontend applies one to a target, expanding it into
exposure plans that the normal /export path writes; nothing here touches the
scheduler database.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from ..plan_templates import PlanTemplate, delete, list_plan_templates, upsert

router = APIRouter()


class PlanTemplateInput(PlanTemplate):
    """Body for create/update; id is optional (generated on create)."""


@router.get("/plan-templates", response_model=list[PlanTemplate])
def get_plan_templates(profile_id: str | None = None) -> list[PlanTemplate]:
    return list_plan_templates(profile_id)


@router.post("/plan-templates", response_model=PlanTemplate)
def create_plan_template(pt: PlanTemplateInput) -> PlanTemplate:
    # Mint a fresh id on create (an empty client-sent id would persist id-less).
    pt.id = uuid.uuid4().hex[:8]
    return upsert(pt)


@router.put("/plan-templates/{pt_id}", response_model=PlanTemplate)
def update_plan_template(pt_id: str, pt: PlanTemplateInput) -> PlanTemplate:
    pt.id = pt_id
    return upsert(pt)


@router.delete("/plan-templates/{pt_id}")
def delete_plan_template(pt_id: str) -> dict:
    if not delete(pt_id):
        raise HTTPException(status_code=404, detail="plan template not found")
    return {"ok": True}
