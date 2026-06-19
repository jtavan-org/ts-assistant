import type { ExposureTemplate } from "./api";

/** Picker label, e.g. "S_900s · S · 900s · Gain: 56". Missing parts are dropped. */
export function templateLabel(t: ExposureTemplate): string {
  const bits = [t.name];
  if (t.filter_name) bits.push(t.filter_name);
  if (t.default_exposure != null) bits.push(`${+t.default_exposure}s`);
  if (t.gain != null && t.gain >= 0) bits.push(`Gain: ${t.gain}`);
  return bits.join(" · ");
}
