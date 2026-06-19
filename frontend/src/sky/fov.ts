// Spherical geometry for drawing a rotated field-of-view rectangle on the sky,
// plus the FOV-from-optics formula (mirrors backend app/astro/fov.py).
//
// Target Scheduler stores a per-target `rotation` (position angle). We draw the
// actual FOV box rotated by that angle so placement *and* orientation are visible.

const DEG = Math.PI / 180;
const RAD = 180 / Math.PI;

/**
 * Destination point on the celestial sphere from (raDeg, decDeg) along position
 * angle `paDeg` (from North through East, astropy convention) for `sepDeg`.
 */
function offset(
  raDeg: number,
  decDeg: number,
  paDeg: number,
  sepDeg: number,
): [number, number] {
  const dec = decDeg * DEG;
  const ra = raDeg * DEG;
  const pa = paDeg * DEG;
  const d = sepDeg * DEG;

  const sinDec2 =
    Math.sin(dec) * Math.cos(d) + Math.cos(dec) * Math.sin(d) * Math.cos(pa);
  const dec2 = Math.asin(Math.max(-1, Math.min(1, sinDec2)));
  const y = Math.sin(pa) * Math.sin(d) * Math.cos(dec);
  const x = Math.cos(d) - Math.sin(dec) * Math.sin(dec2);
  const ra2 = ra + Math.atan2(y, x);
  return [(((ra2 * RAD) % 360) + 360) % 360, dec2 * RAD];
}

/**
 * Map a box-local offset to RA/Dec. `u` runs along the width axis (PA+90),
 * `v` along the height axis (PA = rotation). Degrees throughout.
 */
function localToRaDec(
  raDeg: number,
  decDeg: number,
  rotationDeg: number,
  u: number,
  v: number,
): [number, number] {
  const sep = Math.hypot(u, v);
  if (sep === 0) return [raDeg, decDeg];
  const pa = rotationDeg + Math.atan2(u, v) * RAD;
  return offset(raDeg, decDeg, pa, sep);
}

/** Four corners of a widthDeg x heightDeg rectangle rotated by rotationDeg. */
export function fovCorners(
  raDeg: number,
  decDeg: number,
  widthDeg: number,
  heightDeg: number,
  rotationDeg: number,
): [number, number][] {
  const w = widthDeg / 2;
  const h = heightDeg / 2;
  return (
    [
      [+w, +h],
      [-w, +h],
      [-w, -h],
      [+w, -h],
    ] as [number, number][]
  ).map(([u, v]) => localToRaDec(raDeg, decDeg, rotationDeg, u, v));
}

/**
 * A small triangle sitting on the middle of the top edge, pointing outward (up),
 * so the box orientation is unambiguous at a glance.
 */
export function fovTopTriangle(
  raDeg: number,
  decDeg: number,
  widthDeg: number,
  heightDeg: number,
  rotationDeg: number,
): [number, number][] {
  const h = heightDeg / 2;
  const s = Math.min(widthDeg, heightDeg) * 0.1; // triangle size
  return [
    localToRaDec(raDeg, decDeg, rotationDeg, 0, h + s), // apex (outward)
    localToRaDec(raDeg, decDeg, rotationDeg, -s * 0.8, h), // base left
    localToRaDec(raDeg, decDeg, rotationDeg, +s * 0.8, h), // base right
  ];
}

export interface FovCalc {
  plateScaleArcsecPerPx: number;
  fovWidthDeg: number;
  fovHeightDeg: number;
}

/** FOV from optics — mirrors backend app/astro/fov.py for live preview. */
export function computeFov(
  pixelSizeUm: number,
  sensorPxW: number,
  sensorPxH: number,
  focalLengthMm: number,
  correctorMag: number,
): FovCalc {
  const eff = focalLengthMm * correctorMag;
  if (eff <= 0 || pixelSizeUm <= 0) {
    return { plateScaleArcsecPerPx: 0, fovWidthDeg: 0, fovHeightDeg: 0 };
  }
  const plate = (206.265 * pixelSizeUm) / eff; // arcsec/px
  return {
    plateScaleArcsecPerPx: plate,
    fovWidthDeg: (plate * sensorPxW) / 3600,
    fovHeightDeg: (plate * sensorPxH) / 3600,
  };
}
