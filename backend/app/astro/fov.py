"""Field-of-view computation from sensor + optics.

Canonical (tested) implementation; the frontend mirrors this for live preview.
"""

from __future__ import annotations

from dataclasses import dataclass

# arcsec per radian-ish constant: 206265 = 180*3600/pi.
ARCSEC_PER_MM_RATIO = 206.265  # (arcsec/px) = 206.265 * pixel_um / focal_mm


@dataclass
class FovResult:
    plate_scale_arcsec_per_px: float
    fov_width_deg: float
    fov_height_deg: float
    effective_focal_length_mm: float


def compute_fov(
    pixel_size_um: float,
    sensor_px_w: int,
    sensor_px_h: int,
    focal_length_mm: float,
    corrector_mag: float = 1.0,
) -> FovResult:
    """Compute plate scale and sensor field of view.

    A corrector/reducer/barlow multiplies the focal length: a 0.8x reducer gives
    a wider field, a 2x barlow a narrower one. ``corrector_mag`` defaults to 1.0
    (e.g. a flat field corrector with no focal-length change).
    """
    eff = focal_length_mm * corrector_mag
    if eff <= 0 or pixel_size_um <= 0:
        return FovResult(0.0, 0.0, 0.0, eff)
    plate = ARCSEC_PER_MM_RATIO * pixel_size_um / eff  # arcsec/pixel
    fov_w = plate * sensor_px_w / 3600.0  # arcsec -> degrees
    fov_h = plate * sensor_px_h / 3600.0
    return FovResult(plate, fov_w, fov_h, eff)
