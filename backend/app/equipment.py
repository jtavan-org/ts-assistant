"""Equipment profile store (app-local, persisted to data/equipment.json).

Profiles describe a sensor + optics combination from which we derive the imaging
field of view. Stored outside the Target Scheduler database (app-local data).
"""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, Field

from .astro.fov import compute_fov
from .config import DATA_DIR, ensure_dirs

EQUIPMENT_FILE = DATA_DIR / "equipment.json"


class EquipmentProfile(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    pixel_size_um: float
    sensor_px_w: int
    sensor_px_h: int
    focal_length_mm: float
    corrector_mag: float = 1.0


class EquipmentWithFov(EquipmentProfile):
    plate_scale_arcsec_per_px: float
    fov_width_deg: float
    fov_height_deg: float


def _with_fov(p: EquipmentProfile) -> EquipmentWithFov:
    f = compute_fov(
        p.pixel_size_um,
        p.sensor_px_w,
        p.sensor_px_h,
        p.focal_length_mm,
        p.corrector_mag,
    )
    return EquipmentWithFov(
        **p.model_dump(),
        plate_scale_arcsec_per_px=f.plate_scale_arcsec_per_px,
        fov_width_deg=f.fov_width_deg,
        fov_height_deg=f.fov_height_deg,
    )


_DEFAULT_SEED = [
    EquipmentProfile(
        name="ASI2600 + 530mm (f/5)",
        pixel_size_um=3.76,
        sensor_px_w=6248,
        sensor_px_h=4176,
        focal_length_mm=530.0,
        corrector_mag=1.0,
    )
]


def _read() -> list[EquipmentProfile]:
    ensure_dirs()
    if not EQUIPMENT_FILE.is_file():
        _write(_DEFAULT_SEED)
        return list(_DEFAULT_SEED)
    raw = json.loads(EQUIPMENT_FILE.read_text() or "[]")
    return [EquipmentProfile(**r) for r in raw]


def _write(profiles: list[EquipmentProfile]) -> None:
    ensure_dirs()
    EQUIPMENT_FILE.write_text(
        json.dumps([p.model_dump() for p in profiles], indent=2)
    )


def list_profiles() -> list[EquipmentWithFov]:
    return [_with_fov(p) for p in _read()]


def upsert(profile: EquipmentProfile) -> EquipmentWithFov:
    profiles = _read()
    for i, p in enumerate(profiles):
        if p.id == profile.id:
            profiles[i] = profile
            break
    else:
        profiles.append(profile)
    _write(profiles)
    return _with_fov(profile)


def delete(profile_id: str) -> bool:
    profiles = _read()
    kept = [p for p in profiles if p.id != profile_id]
    if len(kept) == len(profiles):
        return False
    _write(kept)
    return True
