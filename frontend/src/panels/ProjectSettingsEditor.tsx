import type { ProjectSettings } from "../api";

interface Props {
  settings: ProjectSettings;
  onChange: (s: ProjectSettings) => void;
  /** When given, shows a "Reset" action restoring these values. */
  defaults?: ProjectSettings;
  /** Open the panel by default (collapsed otherwise). */
  open?: boolean;
  /** True when the project has a custom exposure order (m74): smart order overrides it. */
  hasOverrideOrder?: boolean;
}

type NumKey =
  | "minimum_time"
  | "minimum_altitude"
  | "maximum_altitude"
  | "horizon_offset"
  | "meridian_window"
  | "filter_switch_frequency"
  | "dither_every"
  | "flats_handling";

const NUM_FIELDS: { key: NumKey; label: string; title?: string }[] = [
  { key: "minimum_time", label: "Minimum time (min)" },
  { key: "minimum_altitude", label: "Minimum altitude (°)" },
  { key: "maximum_altitude", label: "Maximum altitude (°)", title: "0 = no maximum" },
  { key: "horizon_offset", label: "Horizon offset (°)" },
  { key: "meridian_window", label: "Meridian window (min)" },
  { key: "filter_switch_frequency", label: "Filter switch frequency" },
  { key: "dither_every", label: "Dither every" },
  { key: "flats_handling", label: "Flats handling", title: "0 = off" },
];

type BoolKey = "use_custom_horizon" | "enable_grader" | "smart_exposure_order";
const BOOL_FIELDS: { key: BoolKey; label: string }[] = [
  { key: "use_custom_horizon", label: "Use custom horizon" },
  { key: "enable_grader", label: "Enable grader" },
  { key: "smart_exposure_order", label: "Smart exposure order" },
];

/**
 * Editor for a project's advanced (NINA project-tab) settings (psq). Pure/controlled
 * — given a settings object + onChange — so it works for both a new-project draft and
 * editing an existing project. The parent supplies the values (NINA defaults for new).
 */
export default function ProjectSettingsEditor({
  settings,
  onChange,
  defaults,
  open,
  hasOverrideOrder,
}: Props) {
  const set = <K extends keyof ProjectSettings>(key: K, value: ProjectSettings[K]) =>
    onChange({ ...settings, [key]: value });

  const dirty =
    !!defaults &&
    (Object.keys(defaults) as (keyof ProjectSettings)[]).some((k) => settings[k] !== defaults[k]);

  return (
    <details className="rule-weights" open={open}>
      <summary>
        <span className="rw-title">Advanced settings</span>
        {dirty && <span className="rw-edited">edited</span>}
      </summary>
      <div className="rw-body">
        <p className="rw-hint">Target Scheduler project options. Defaults match NINA.</p>

        <label className="rw-row">
          <span className="rw-name">Priority</span>
          <select
            value={settings.priority}
            onChange={(e) => set("priority", Number(e.target.value))}
          >
            <option value={0}>Low</option>
            <option value={1}>Normal</option>
            <option value={2}>High</option>
          </select>
        </label>

        {NUM_FIELDS.map((f) => (
          <label className="rw-row" key={f.key} title={f.title}>
            <span className="rw-name">{f.label}</span>
            <input
              className="rw-num"
              type="number"
              value={settings[f.key]}
              onChange={(e) => set(f.key, Number(e.target.value))}
            />
          </label>
        ))}

        {BOOL_FIELDS.map((f) => (
          <div key={f.key}>
            <label className="rw-row">
              <span className="rw-name">{f.label}</span>
              <input
                type="checkbox"
                checked={settings[f.key]}
                onChange={(e) => set(f.key, e.target.checked)}
              />
            </label>
            {f.key === "smart_exposure_order" &&
              settings.smart_exposure_order &&
              hasOverrideOrder && (
                <p className="rw-warn">
                  Smart exposure order overrides your custom exposure order — turn it off
                  to use the order you set below.
                </p>
              )}
          </div>
        ))}

        {defaults && (
          <button
            type="button"
            className="rw-reset"
            disabled={!dirty}
            onClick={() => onChange({ ...defaults })}
          >
            Reset to defaults
          </button>
        )}
      </div>
    </details>
  );
}
