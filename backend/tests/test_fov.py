"""Tests for FOV computation."""

import pytest

from app.astro.fov import compute_fov


def test_plate_scale_and_fov_asi2600_530mm():
    # ASI2600 (3.76 µm, 6248x4176) at 530 mm, no corrector.
    r = compute_fov(3.76, 6248, 4176, 530.0, 1.0)
    # plate scale = 206.265 * 3.76 / 530 = 1.4633 arcsec/px
    assert r.plate_scale_arcsec_per_px == pytest.approx(1.4633, abs=1e-3)
    # fov_w = 1.4633 * 6248 / 3600 = 2.540 deg
    assert r.fov_width_deg == pytest.approx(2.540, abs=1e-2)
    assert r.fov_height_deg == pytest.approx(1.698, abs=1e-2)
    assert r.effective_focal_length_mm == pytest.approx(530.0)


def test_reducer_widens_field():
    base = compute_fov(3.76, 6248, 4176, 530.0, 1.0)
    reduced = compute_fov(3.76, 6248, 4176, 530.0, 0.8)
    # 0.8x reducer -> shorter effective focal length -> wider field.
    assert reduced.fov_width_deg > base.fov_width_deg
    assert reduced.effective_focal_length_mm == pytest.approx(424.0)


def test_barlow_narrows_field():
    base = compute_fov(3.76, 6248, 4176, 530.0, 1.0)
    barlow = compute_fov(3.76, 6248, 4176, 530.0, 2.0)
    assert barlow.fov_width_deg < base.fov_width_deg


def test_degenerate_inputs():
    r = compute_fov(0, 1000, 1000, 0, 1.0)
    assert r.fov_width_deg == 0.0
