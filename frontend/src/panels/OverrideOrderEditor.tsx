import { useState } from "react";
import type { OverrideStep } from "../api";
import type { ExposurePlanDraft } from "./ProjectBuilder";

interface Props {
  steps: OverrideStep[];
  /** The project's exposure plans — referenced by index for "expose" steps. */
  plans: ExposurePlanDraft[];
  onChange: (steps: OverrideStep[]) => void;
  open?: boolean;
}

function planLabel(p: ExposurePlanDraft | undefined, idx: number): string {
  if (!p) return `plan ${idx + 1}`;
  const filter = p.filterName?.trim() || "filter";
  return `${filter} · ${p.exposure}s`;
}

/**
 * Editor for a project's override exposure order (awh): an explicit ordered list of
 * "expose «plan»" / "Dither" steps. Empty = NINA's default cadence. Controlled.
 * Applied to every target on save; steps reference the (shared) exposure plans by index.
 */
export default function OverrideOrderEditor({ steps, plans, onChange, open }: Props) {
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  // Move the step at `from` to position `to` (drag-and-drop reordering).
  const reorder = (from: number, to: number) => {
    if (from === to) return;
    const next = steps.slice();
    const [it] = next.splice(from, 1);
    next.splice(to, 0, it);
    onChange(next);
  };
  const remove = (i: number) => onChange(steps.filter((_, k) => k !== i));
  const setRef = (i: number, ref: number) =>
    onChange(steps.map((s, k) => (k === i ? { ...s, reference_idx: ref } : s)));
  const addExpose = () => onChange([...steps, { action: 0, reference_idx: 0 }]);
  const addDither = () => onChange([...steps, { action: 1, reference_idx: -1 }]);

  return (
    <details className="rule-weights" open={open}>
      <summary>
        <span className="rw-title">Exposure order</span>
        {steps.length > 0 && <span className="rw-edited">custom</span>}
      </summary>
      <div className="rw-body">
        <p className="rw-hint">
          Optional explicit capture order. Leave empty to use NINA's default cadence.
        </p>

        {steps.map((s, i) => (
          <div
            className={"oeo-row" + (dragIdx === i ? " oeo-dragging" : "")}
            key={i}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => {
              if (dragIdx !== null) reorder(dragIdx, i);
              setDragIdx(null);
            }}
          >
            <span
              className="oeo-handle"
              title="Drag to reorder"
              draggable
              onDragStart={() => setDragIdx(i)}
              onDragEnd={() => setDragIdx(null)}
            >
              ⠿
            </span>
            <span className="oeo-num">{i + 1}</span>
            {s.action === 1 ? (
              <span className="oeo-dither">Dither</span>
            ) : (
              <select
                className="oeo-plan"
                value={s.reference_idx}
                onChange={(e) => setRef(i, Number(e.target.value))}
              >
                {plans.map((p, idx) => (
                  <option key={idx} value={idx}>
                    {planLabel(p, idx)}
                  </option>
                ))}
              </select>
            )}
            <button type="button" title="Remove step" onClick={() => remove(i)}>
              ✕
            </button>
          </div>
        ))}

        <div className="oeo-add">
          <button type="button" onClick={addExpose} disabled={plans.length === 0}>
            ＋ Add exposure
          </button>
          <button type="button" onClick={addDither}>
            ＋ Add dither
          </button>
        </div>
      </div>
    </details>
  );
}
