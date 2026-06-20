import type { RuleWeight } from "../api";

interface Props {
  /** The current weights (one row per scoring rule). */
  weights: RuleWeight[];
  /** Emits the full updated list when any weight changes. */
  onChange: (weights: RuleWeight[]) => void;
  /** When given, shows a "Reset" action that restores these values. */
  defaults?: RuleWeight[];
  /** Open the panel by default (collapsed otherwise). */
  open?: boolean;
}

/**
 * Editor for a project's scoring rule weights (qiz.3). Pure/controlled — given a
 * weights list + onChange, so it works both for a new-project draft and (later) for
 * editing an existing project's weights (o2c). It never fetches; the parent supplies
 * the weights (seeded from /api/rule-weight-defaults for new projects).
 */
export default function RuleWeightsEditor({ weights, onChange, defaults, open }: Props) {
  function setWeight(name: string, weight: number) {
    onChange(weights.map((w) => (w.name === name ? { ...w, weight } : w)));
  }

  const dirty =
    !!defaults &&
    weights.some((w) => defaults.find((d) => d.name === w.name)?.weight !== w.weight);

  return (
    <details className="rule-weights" open={open}>
      <summary>
        <span className="rw-title">Rule weights</span>
        {dirty && <span className="rw-edited">edited</span>}
      </summary>
      <div className="rw-body">
        <p className="rw-hint">
          How the scheduler prioritises this project. Defaults match NINA.
        </p>
        {weights.map((w) => (
          <label className="rw-row" key={w.name}>
            <span className="rw-name">{w.name}</span>
            <input
              className="rw-num"
              type="number"
              step={1}
              value={w.weight}
              onChange={(e) => setWeight(w.name, Number(e.target.value))}
            />
          </label>
        ))}
        {defaults && (
          <button
            type="button"
            className="rw-reset"
            disabled={!dirty}
            onClick={() => onChange(defaults.map((d) => ({ ...d })))}
          >
            Reset to defaults
          </button>
        )}
      </div>
    </details>
  );
}
