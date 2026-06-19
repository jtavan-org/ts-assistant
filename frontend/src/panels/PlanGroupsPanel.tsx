import { useState } from "react";
import type { ExposureTemplate, PlanGroup, PlanGroupInput } from "../api";
import { templateLabel } from "../templateLabel";

interface DraftItem {
  templateId: number | null;
  desired: number;
}

interface Props {
  templates: ExposureTemplate[];
  groups: PlanGroup[];
  onCreate: (g: PlanGroupInput) => Promise<PlanGroup>;
  onUpdate: (g: PlanGroup) => Promise<PlanGroup>;
  onDelete: (id: string) => Promise<void>;
}

/** Manage reusable exposure plan groups (qiz.1 Stage 3): a named bundle of
 * template + frame-count rows the Project builder can apply in one pick. */
export default function PlanGroupsPanel({
  templates,
  groups,
  onCreate,
  onUpdate,
  onDelete,
}: Props) {
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("");
  const [items, setItems] = useState<DraftItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  function load(id: string) {
    setSelectedId(id);
    setMsg(null);
    const g = groups.find((x) => x.id === id);
    if (!g) {
      setName("");
      setItems([]);
      return;
    }
    setName(g.name);
    setItems(g.items.map((it) => ({ templateId: it.exposure_template_id, desired: it.desired })));
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
    <details className="plan-groups">
      <summary>
        <span className="eq-title">Plan groups</span>
        {groups.length > 0 && <span className="eq-fov">{groups.length}</span>}
      </summary>

      <div className="eq-body">
        <select
          className="plan-group-apply"
          value={selectedId}
          onChange={(e) => load(e.target.value)}
        >
          <option value="">＋ New group</option>
          {groups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
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
                onChange={(e) =>
                  patchItem(i, { templateId: e.target.value ? Number(e.target.value) : null })
                }
              >
                <option value="">Select Exposure Template</option>
                {templates.map((t) => (
                  <option key={t.id} value={String(t.id)}>
                    {templateLabel(t)}
                  </option>
                ))}
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
            <div className="eq-readout warn">Add filters to this group.</div>
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
            {busy ? "Saving…" : selectedId ? "Save group" : "Create group"}
          </button>
          {selectedId && (
            <button className="target-del" title="Delete group" onClick={remove}>
              🗑
            </button>
          )}
        </div>

        {msg && <div className="eq-readout">{msg}</div>}
      </div>
    </details>
  );
}
