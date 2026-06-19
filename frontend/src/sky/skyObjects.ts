// Bundled catalog of named deep-sky objects for the sky-view overlay (bead cia):
// an extent circle + label per object so the user can orient and spot framing
// targets at a glance.
//
// The data in skyObjects.generated.json is produced by scripts/gen_named_objects.py
// from authoritative online catalogs (so coordinates/sizes are accurate, not
// hand-typed) and committed, so the app stays offline/deterministic at runtime:
//   - Messier (complete, 110)        — OpenNGC
//   - IC highlights (size-filtered)  — OpenNGC
//   - Sharpless Sh2 HII regions      — VizieR VII/20
//   - Large supernova remnants       — VizieR VII/284 (Green 2019)
//   - A few famous NGC-only showpieces (North America, Helix, ...)
// Re-run the generator to refresh; do not hand-edit the JSON.
//
// Coordinates are J2000 in DEGREES; `sizeArcmin` is the major angular axis.
import generated from "./skyObjects.generated.json";

/** Which source catalog an object came from. */
export type Catalog = "M" | "C" | "IC" | "Sh2" | "SNR" | "NGC";

export interface SkyObject {
  /** Primary catalog id, e.g. "M31", "Sh2-155", "Cygnus Loop". */
  id: string;
  /** Common name, e.g. "Andromeda Galaxy". Empty if the id is already the name. */
  name: string;
  /** Right ascension, J2000, degrees. */
  ra: number;
  /** Declination, J2000, degrees. */
  dec: number;
  /** Major angular axis, arcminutes (drives the circle radius). */
  sizeArcmin: number;
  /** Coarse object class, for the popup. */
  kind: "galaxy" | "nebula" | "cluster" | "planetary" | "supernova";
  /** Source catalog. */
  catalog: Catalog;
}

/** Label to render for an object: "<id> <name>" when it has a distinct name. */
export function objectLabel(o: SkyObject): string {
  return o.name ? `${o.id} ${o.name}` : o.id;
}

export const NAMED_OBJECTS: SkyObject[] = generated as SkyObject[];
