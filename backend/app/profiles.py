"""Profile aliases (app-local, persisted to data/profiles.json).

Target Scheduler is multi-profile, but the scheduler database stores only profile
*GUIDs* — there is no human-readable profile name anywhere in it (NINA keeps profile
names in its own config, outside this database). To make a profile usable as the
top-level organizing axis, TS Assistant lets the user attach a friendly *alias* to a
profile id. Aliases are an app-local convenience; they are never written back to NINA.

Stored outside the Target Scheduler database (app-local data), mirroring the equipment
and plan-template stores. No seed: a profile with no alias falls back to a truncated GUID.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from .config import DATA_DIR, ensure_dirs

PROFILES_FILE = DATA_DIR / "profiles.json"


class ProfileAlias(BaseModel):
    """A user-chosen friendly name for a NINA profile id (GUID)."""

    id: str
    name: str


def _read() -> list[ProfileAlias]:
    ensure_dirs()
    if not PROFILES_FILE.is_file():
        return []
    raw = json.loads(PROFILES_FILE.read_text() or "[]")
    return [ProfileAlias(**r) for r in raw]


def _write(aliases: list[ProfileAlias]) -> None:
    ensure_dirs()
    PROFILES_FILE.write_text(json.dumps([a.model_dump() for a in aliases], indent=2))


def get_aliases() -> dict[str, str]:
    """Map profile_id -> friendly name for every aliased profile."""
    return {a.id: a.name for a in _read()}


def set_alias(profile_id: str, name: str) -> ProfileAlias:
    """Upsert the alias for ``profile_id`` and return it."""
    alias = ProfileAlias(id=profile_id, name=name)
    aliases = _read()
    for i, existing in enumerate(aliases):
        if existing.id == profile_id:
            aliases[i] = alias
            break
    else:
        aliases.append(alias)
    _write(aliases)
    return alias
