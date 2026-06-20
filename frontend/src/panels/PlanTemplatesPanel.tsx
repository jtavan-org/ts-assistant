import { useState } from "react";
import type { ExposureTemplate, PlanTemplate, PlanTemplateInput } from "../api";
import { templateLabel } from "../templateLabel";

interface DraftItem {
  templateId: number | null;
  desired: number;
}

interface Props {
  templates: ExposureTemplate[];
  planTemplates: PlanTemplate[];
  onCreate: (pt: PlanTemplateInput) => Promise<PlanTemplate>;
  onUpdate: (pt: PlanTemplate) => Promise<PlanTemplate>;
  onDelete: (id: string) => Promise<void>;
  onRequestNewTemplate: () => Promise<ExposureTemplate | null>;
}

/** Manage reusable exposure plan templates (qiz.1 Stage 3): a named bundle of
 * exposure-template + frame-count rows the Project builder can apply in one pick. */
export default function PlanTemplatesPanel({
  templates,
  planTemplates,
  onCreate,
  onUpdate,
  onDelete,
  onRequestNewTemplate,
}: Props) {
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("");
  const [items, setItems] = useState<DraftItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  function load(id: string) {
    setSelectedId(id);
    setMsg(null);
    const pt = planTemplates.find((x) => x.id === id);
    if (!pt) {
      setName("");
      setItems([]);
      return;
    }
    setName(pt.name);
    setItems(pt.items.map((it) => ({ templateId: it.exposure_template_id, desired: it.desired })));
  }

  function patchItem(i: number, patch: Partial<DraftItem>) {
    setItems((xs) => xs.map((it, j) => (j === i ? { ...it, ...patch } : it)));
  }

  const validItems = items.filter((it) => it.templateId != null);
  const canSave = !!name.trim() && validItems.length > 0 && !busy;

  async function save() {
    if (!canSave) return;
    setBusy(true);
    setMsg(null);
    const payload = {
      name: name.trim(),
      items: validItems.map((it) => ({
        exposure_template_id: it.templateId as number,
        desired: it.desired,
      })),
    };
    try {
      if (selectedId) {
        await onUpdate({ id: selectedId, ...payload });
        setMsg(`Saved “${payload.name}”.`);
      } else {
        const created = await onCreate(payload);
        setSelectedId(created.id);
        setMsg(`Created “${payload.name}”.`);
      }
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!selectedId) return;
    setBusy(true);
    setMsg(null);
    try {
      await onDelete(selectedId);
      load("");
      setMsg("Deleted.");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="plan-templates">
      <summary>
        <span className="eq-title">Exposure plan templates</span>
        {planTemplates.length > 0 && (
          <span className="eq-fov">{planTemplates.length}</span>
        )}
      </summary>

      <div className="eq-body">
        <select
          className="plan-template-apply"
          value={selectedId}
          onChange={(e) => load(e.target.value)}
        >
          <option value="">＋ New plan template</option>
          {planTemplates.map((pt) => (
            <option key={pt.id} value={pt.id}>
              {pt.name}
            </option>
          ))}
        </select>

        {templates.length === 0 && (
          <div className="eq-readout warn">No exposure templates available.</div>
        )}

        <label className="eq-field eq-name">
          Name
          <input
            value={name}
            placeholder="e.g. LRGB Dark Nebula"
            onChange={(e) => setName(e.target.value)}
          />
        </label>

        <div className="plan-list">
          {items.map((it, i) => (
            <div className="plan-row" key={i}>
              <select
                className="plan-template"
                value={it.templateId != null ? String(it.templateId) : ""}
                onChange={async (e) => {
                  const v = e.target.value;
                  if (v === "__new__") {
                    const t = await onRequestNewTemplate();
                    if (t) patchItem(i, { templateId: t.id });
                    return;
                  }
                  patchItem(i, { templateId: v ? Number(v) : null });
                }}
              >
                <option value="">Select Exposure Template</option>
                {templates.map((t) => (
                  <option key={t.id} value={String(t.id)}>
                    {templateLabel(t)}
                  </option>
                ))}
                <option value="__new__">＋ New template…</option>
              </select>
              <input
                className="plan-num"
                type="number"
                min={1}
                value={it.desired}
                title="desired frames"
                onChange={(e) =>
                  patchItem(i, { desired: Math.max(1, Math.round(Number(e.target.value))) })
                }
              />
              <span className="plan-unit">×</span>
              <button
                className="target-del"
                title="Remove filter"
                onClick={() => setItems((xs) => xs.filter((_, j) => j !== i))}
              >
                ✕
              </button>
            </div>
          ))}
          {!items.length && (
            <div className="eq-readout warn">Add filters to this plan template.</div>
          )}
        </div>

        <div className="eq-row">
          <button
            onClick={() => setItems((xs) => [...xs, { templateId: null, desired: 20 }])}
            disabled={templates.length === 0}
          >
            ＋ Add filter
          </button>
        </div>

        <div className="eq-row">
          <button className="eq-save" disabled={!canSave} onClick={save}>
            {busy ? "Saving…" : selectedId ? "Save plan template" : "Create plan template"}
          </button>
          {selectedId && (
            <button className="target-del" title="Delete plan template" onClick={remove}>
              🗑
            </button>
          )}
        </div>

        {msg && <div className="eq-readout">{msg}</div>}
      </div>
    </details>
  );
}
