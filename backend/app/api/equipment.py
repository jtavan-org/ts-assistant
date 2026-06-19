"""Equipment profile CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..equipment import (
    EquipmentProfile,
    EquipmentWithFov,
    delete,
    list_profiles,
    upsert,
)

router = APIRouter()


class EquipmentInput(EquipmentProfile):
    """Body for create/update; id is optional (generated on create)."""


@router.get("/equipment", response_model=list[EquipmentWithFov])
def get_equipment() -> list[EquipmentWithFov]:
    return list_profiles()


@router.post("/equipment", response_model=EquipmentWithFov)
def create_equipment(profile: EquipmentInput) -> EquipmentWithFov:
    return upsert(profile)


@router.put("/equipment/{profile_id}", response_model=EquipmentWithFov)
def update_equipment(profile_id: str, profile: EquipmentInput) -> EquipmentWithFov:
    profile.id = profile_id
    return upsert(profile)


@router.delete("/equipment/{profile_id}")
def delete_equipment(profile_id: str) -> dict:
    if not delete(profile_id):
        raise HTTPException(status_code=404, detail="profile not found")
    return {"ok": True}
