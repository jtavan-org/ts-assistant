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

/** Great-circle angular separation between two sky points (degrees). */
export function angularDistance(
  ra1: number,
  dec1: number,
  ra2: number,
  dec2: number,
): number {
  const d1 = dec1 * DEG;
  const d2 = dec2 * DEG;
  const dRa = (ra2 - ra1) * DEG;
  const c = Math.max(
    -1,
    Math.min(1, Math.sin(d1) * Math.sin(d2) + Math.cos(d1) * Math.cos(d2) * Math.cos(dRa)),
  );
  return Math.acos(c) * RAD;
}

/** Position angle (North through East, degrees, 0..360) from point 1 toward point 2. */
export function positionAngle(
  ra1: number,
  dec1: number,
  ra2: number,
  dec2: number,
): number {
  const d1 = dec1 * DEG;
  const d2 = dec2 * DEG;
  const dRa = (ra2 - ra1) * DEG;
  const y = Math.sin(dRa) * Math.cos(d2);
  const x = Math.cos(d1) * Math.sin(d2) - Math.sin(d1) * Math.cos(d2) * Math.cos(dRa);
  return ((Math.atan2(y, x) * RAD) % 360 + 360) % 360;
}

/** Midpoint along the great circle between two sky points. */
export function sphericalMidpoint(
  ra1: number,
  dec1: number,
  ra2: number,
  dec2: number,
): [number, number] {
  const sep = angularDistance(ra1, dec1, ra2, dec2);
  if (sep === 0) return [ra1, dec1];
  return offset(ra1, dec1, positionAngle(ra1, dec1, ra2, dec2), sep / 2);
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

/** One panel of a mosaic: its grid position, center, and rotated FOV corners. */
export interface MosaicPanel {
  row: number;
  col: number;
  centerRa: number;
  centerDec: number;
  corners: [number, number][];
}

/**
 * Lay out an `cols` x `rows` grid of FOV panels (each panelWidthDeg x panelHeightDeg)
 * centered on (centerRa, centerDec), with `overlapPct` overlap between neighbours and
 * the whole grid rotated by `rotationDeg` (position angle, North->East).
 *
 * Panels share the mosaic position angle so the grid stays aligned. Row 0 is the top
 * (+height/North-ish before rotation), col 0 is the left. The per-panel centers are the
 * exact RA/Dec hand-off the future write path persists as Target rows.
 */
export function mosaicPanels(
  centerRa: number,
  centerDec: number,
  panelWidthDeg: number,
  panelHeightDeg: number,
  cols: number,
  rows: number,
  overlapPct: number,
  rotationDeg: number,
): MosaicPanel[] {
  const f = 1 - overlapPct / 100; // fraction of a panel between adjacent centers
  const stepW = panelWidthDeg * f;
  const stepH = panelHeightDeg * f;
  const panels: MosaicPanel[] = [];
  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const u = (col - (cols - 1) / 2) * stepW; // +u = right (along PA+90)
      const v = ((rows - 1) / 2 - row) * stepH; // +v = top (along PA)
      const [ra, dec] = localToRaDec(centerRa, centerDec, rotationDeg, u, v);
      panels.push({
        row,
        col,
        centerRa: ra,
        centerDec: dec,
        corners: fovCorners(ra, dec, panelWidthDeg, panelHeightDeg, rotationDeg),
      });
    }
  }
  return panels;
}

/** Minimum number of panes (each paneDeg wide) to fully cover `coverageDeg`. */
export function panesToCover(
  coverageDeg: number,
  paneDeg: number,
  overlapPct: number,
): number {
  if (paneDeg <= 0 || coverageDeg <= paneDeg) return 1;
  const step = paneDeg * (1 - overlapPct / 100); // center-to-center spacing
  if (step <= 0) return 1;
  return 1 + Math.ceil((coverageDeg - paneDeg) / step);
}

/**
 * Given the four sky corners of a dragged Area-of-Interest rectangle, derive the
 * mosaic that fully covers it at the current pane FOV: center, panes (NxM) and the
 * rotation/position-angle of the AoI itself. Corners are [ra,dec]; tl/tr/bl/br are
 * the screen top-left/top-right/bottom-left/bottom-right after un-projection.
 */
export function coverageToGrid(
  tl: [number, number],
  tr: [number, number],
  bl: [number, number],
  br: [number, number],
  paneWidthDeg: number,
  paneHeightDeg: number,
  overlapPct: number,
): {
  centerRa: number;
  centerDec: number;
  cols: number;
  rows: number;
  rotationDeg: number;
} {
  const [centerRa, centerDec] = sphericalMidpoint(tl[0], tl[1], br[0], br[1]);
  const width =
    (angularDistance(tl[0], tl[1], tr[0], tr[1]) +
      angularDistance(bl[0], bl[1], br[0], br[1])) /
    2;
  const height =
    (angularDistance(tl[0], tl[1], bl[0], bl[1]) +
      angularDistance(tr[0], tr[1], br[0], br[1])) /
    2;
  // Rotation = PA of the AoI's "up" axis (bottom-edge midpoint -> top-edge midpoint),
  // matching the height-axis convention fovCorners()/localToRaDec() use.
  const [bRa, bDec] = sphericalMidpoint(bl[0], bl[1], br[0], br[1]);
  const [tRa, tDec] = sphericalMidpoint(tl[0], tl[1], tr[0], tr[1]);
  const rotationDeg = positionAngle(bRa, bDec, tRa, tDec);
  return {
    centerRa,
    centerDec,
    cols: panesToCover(width, paneWidthDeg, overlapPct),
    rows: panesToCover(height, paneHeightDeg, overlapPct),
    rotationDeg,
  };
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
