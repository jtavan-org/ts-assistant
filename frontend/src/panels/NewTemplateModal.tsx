import { useState } from "react";
import type { ExposureTemplate, ExposureTemplateInput } from "../api";
import { templateLabel } from "../templateLabel";

interface Props {
  templates: ExposureTemplate[];
  profileId: string;
  onSubmit: (input: ExposureTemplateInput) => Promise<void>;
  onClose: () => void;
}

const INITIAL = {
  name: "",
  filterName: "",
  gain: "",
  offset: "",
  binning: "1",
  defaultExposure: "60",
  readoutMode: "",
  twilightLevel: "0",
  moonAvoidanceEnabled: false,
  moonAvoidanceSeparation: "0",
  moonAvoidanceWidth: "0",
  maximumHumidity: "",
  ditherEvery: "",
  minutesOffset: "0",
  moonRelaxScale: "0",
  moonRelaxMaxAltitude: "5",
  moonRelaxMinAltitude: "-15",
  moonDownEnabled: false,
};

const numOrNull = (s: string) => (s.trim() === "" ? null : Number(s));
const intOr = (s: string, d: number) => (s.trim() === "" ? d : Math.round(Number(s)));
const numOr = (s: string, d: number) => (s.trim() === "" ? d : Number(s));
// NINA's "use the default" sentinel is -1 (camera default for gain/offset/readout,
// project default for dither). We show those blank and store them as -1.
const DEFAULT_SENTINEL = -1;
// A stored value is "default" when it's null or the -1 sentinel -> show blank.
const fromSentinel = (v: number | null | undefined) =>
  v == null || v < 0 ? "" : String(v);

/** Lightweight modal to create one exposure template (qiz.5). Essentials are
 * always visible; the rest live under a collapsed "Advanced" with NINA defaults. */
export default function NewTemplateModal({ templates, profileId, onSubmit, onClose }: Props) {
  const [f, setF] = useState({ ...INITIAL });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (patch: Partial<typeof INITIAL>) => setF((x) => ({ ...x, ...patch }));

  const canCreate = !!f.name.trim() && !!f.filterName.trim() && !busy;

  function baseOn(id: string) {
    const t = templates.find((x) => x.id === Number(id));
    if (!t) {
      setF({ ...INITIAL });
      return;
    }
    set({
      name: t.name ? `${t.name} copy` : "",
      filterName: t.filter_name ?? "",
      // gain/offset/readout/dither: -1 (or null) means "use default" -> show blank
      gain: fromSentinel(t.gain),
      offset: fromSentinel(t.offset),
      binning: t.binning != null ? String(t.binning) : "1",
      defaultExposure: t.default_exposure != null ? String(t.default_exposure) : "60",
      readoutMode: fromSentinel(t.readout_mode),
      twilightLevel: t.twilight_level != null ? String(t.twilight_level) : "0",
      moonAvoidanceEnabled: !!t.moon_avoidance_enabled,
      moonAvoidanceSeparation:
        t.moon_avoidance_separation != null ? String(t.moon_avoidance_separation) : "0",
      moonAvoidanceWidth: t.moon_avoidance_width != null ? String(t.moon_avoidance_width) : "0",
      maximumHumidity: t.maximum_humidity != null ? String(t.maximum_humidity) : "",
      ditherEvery: fromSentinel(t.dither_every),
      minutesOffset: t.minutes_offset != null ? String(t.minutes_offset) : "0",
    });
  }

  async function create() {
    if (!canCreate) return;
    setBusy(true);
    setError(null);
    const input: ExposureTemplateInput = {
      profile_id: profileId,
      name: f.name.trim(),
      filter_name: f.filterName.trim(),
      gain: intOr(f.gain, DEFAULT_SENTINEL),
      offset: intOr(f.offset, DEFAULT_SENTINEL),
      binning: intOr(f.binning, 1),
      readout_mode: intOr(f.readoutMode, DEFAULT_SENTINEL),
      twilight_level: intOr(f.twilightLevel, 0),
      moon_avoidance_enabled: f.moonAvoidanceEnabled,
      moon_avoidance_separation: numOr(f.moonAvoidanceSeparation, 0),
      moon_avoidance_width: intOr(f.moonAvoidanceWidth, 0),
      maximum_humidity: numOrNull(f.maximumHumidity),
      default_exposure: numOr(f.defaultExposure, 60),
      moon_relax_scale: numOr(f.moonRelaxScale, 0),
      moon_relax_max_altitude: numOr(f.moonRelaxMaxAltitude, 5),
      moon_relax_min_altitude: numOr(f.moonRelaxMinAltitude, -15),
      moon_down_enabled: f.moonDownEnabled,
      dither_every: intOr(f.ditherEvery, -1),
      minutes_offset: intOr(f.minutesOffset, 0),
    };
    try {
      await onSubmit(input);
      // onSubmit closes the modal on success.
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">New exposure template</div>

        {templates.length > 0 && (
          <label className="eq-field">
            Base on
            <select className="plan-group-apply" defaultValue="" onChange={(e) => baseOn(e.target.value)}>
              <option value="">— blank —</option>
              {templates.map((t) => (
                <option key={t.id} value={String(t.id)}>
                  {templateLabel(t)}
                </option>
              ))}
            </select>
          </label>
        )}

        <div className="tmpl-grid">
          <label className="eq-field">
            Name
            <input value={f.name} placeholder="e.g. Ha 3nm 900s" onChange={(e) => set({ name: e.target.value })} />
          </label>
          <label className="eq-field">
            Filter
            <input value={f.filterName} placeholder="Ha" onChange={(e) => set({ filterName: e.target.value })} />
          </label>
          <label className="eq-field">
            Gain
            <input value={f.gain} placeholder="(camera default)" onChange={(e) => set({ gain: e.target.value })} />
          </label>
          <label className="eq-field">
            Offset
            <input value={f.offset} placeholder="(camera default)" onChange={(e) => set({ offset: e.target.value })} />
          </label>
          <label className="eq-field">
            Binning
            <input value={f.binning} onChange={(e) => set({ binning: e.target.value })} />
          </label>
          <label className="eq-field">
            Exposure (s)
            <input value={f.defaultExposure} onChange={(e) => set({ defaultExposure: e.target.value })} />
          </label>
        </div>

        <details className="tmpl-advanced">
          <summary>Advanced options</summary>
          <div className="tmpl-grid">
            <label className="eq-field">
              Readout mode
              <input value={f.readoutMode} placeholder="(default)" onChange={(e) => set({ readoutMode: e.target.value })} />
            </label>
            <label className="eq-field">
              Twilight level
              <input value={f.twilightLevel} onChange={(e) => set({ twilightLevel: e.target.value })} />
            </label>
            <label className="eq-field eq-check">
              <input
                type="checkbox"
                checked={f.moonAvoidanceEnabled}
                onChange={(e) => set({ moonAvoidanceEnabled: e.target.checked })}
              />
              Moon avoidance
            </label>
            <label className="eq-field">
              Moon sep (°)
              <input
                value={f.moonAvoidanceSeparation}
                onChange={(e) => set({ moonAvoidanceSeparation: e.target.value })}
              />
            </label>
            <label className="eq-field">
              Moon width
              <input value={f.moonAvoidanceWidth} onChange={(e) => set({ moonAvoidanceWidth: e.target.value })} />
            </label>
            <label className="eq-field">
              Max humidity
              <input value={f.maximumHumidity} placeholder="(none)" onChange={(e) => set({ maximumHumidity: e.target.value })} />
            </label>
            <label className="eq-field">
              Dither every
              <input
                value={f.ditherEvery}
                placeholder="(project default)"
                onChange={(e) => set({ ditherEvery: e.target.value })}
              />
            </label>
            <label className="eq-field">
              Minutes offset
              <input value={f.minutesOffset} onChange={(e) => set({ minutesOffset: e.target.value })} />
            </label>
          </div>
        </details>

        {error && <div className="eq-readout warn">{error}</div>}

        <div className="modal-actions">
          <button onClick={onClose}>Cancel</button>
          <button className="eq-save" disabled={!canCreate} onClick={create}>
            {busy ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}
