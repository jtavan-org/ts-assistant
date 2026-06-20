"""NINA profile endpoints (bead bg0).

The scheduler database identifies profiles only by GUID; TS Assistant exposes the
distinct ids present in the DB, joined with any user-chosen aliases (app-local) so the
frontend can offer a human-readable, top-level profile picker.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import profiles as profiles_store
from ..db.reader import load_profile_ids

router = APIRouter()


class ProfileInfo(BaseModel):
    """A profile id with its display name (alias, or a truncated GUID fallback)."""

    id: str
    name: str


class AliasInput(BaseModel):
    name: str


def _display_name(profile_id: str, aliases: dict[str, str]) -> str:
    return aliases.get(profile_id) or profile_id[:8]


@router.get("/profiles", response_model=list[ProfileInfo])
def get_profiles() -> list[ProfileInfo]:
    aliases = profiles_store.get_aliases()
    return [
        ProfileInfo(id=pid, name=_display_name(pid, aliases))
        for pid in load_profile_ids()
    ]


@router.put("/profiles/{profile_id}", response_model=ProfileInfo)
def set_profile_alias(profile_id: str, body: AliasInput) -> ProfileInfo:
    alias = profiles_store.set_alias(profile_id, body.name)
    return ProfileInfo(id=alias.id, name=alias.name)
