import type { ExposureTemplate, PlanTemplate, RuleWeight } from "../api";
import { templateLabel } from "../templateLabel";
import RuleWeightsEditor from "./RuleWeightsEditor";
import type { FovBox, PlaceMode } from "../sky/AladinView";

/** One target being framed: a single pointing (1×1) or a mosaic (N×M panes). */
export interface TargetDraft {
  id: string;
  /** Existing DB target id when editing a saved project (o2c); undefined for new. */
  dbId?: number;
  name: string;
  centerRa: number;
  centerDec: number;
  cols: number;
  rows: number;
  overlapPct: number;
  rotationDeg: number;
}

/** One exposure plan for the project: a filter, sub-exposure, and frame count.
 * May reference an existing Target Scheduler exposure template by id (qiz.1);
 * when null, the filter name auto-creates a bare template on export. */
export interface ExposurePlanDraft {
  id: string;
  filterName: string;
  exposure: number;
  desired: number;
  exposureTemplateId: number | null;
}

/** A draft Project: the top-tier artifact, holding one or more targets. */
export interface ProjectDraft {
  name: string;
  profileId: string;
  targets: TargetDraft[];
  activeTargetId: string | null;
  /** Applied to every target/pane on export (the mosaic-imaging common case). */
  exposurePlans: ExposurePlanDraft[];
  /** Scoring rule weights for the project (qiz.3); seeded from NINA defaults. */
  ruleWeights: RuleWeight[];
}

interface Props {
  /** Current rig FOV = the size of one pane; null until a rig is selected. */
  fov: FovBox | null;
  draft: ProjectDraft | null;
  placeMode: PlaceMode;
  /** Existing exposure templates from the DB; one must be picked per plan. */
  templates: ExposureTemplate[];
  /** Saved exposure plan templates; applying one fills the plans in a single pick. */
  planTemplates: PlanTemplate[];
  saving: boolean;
  /** True when editing an existing project (o2c) rather than creating a new one. */
  editing: boolean;
  saveResult: { ok: boolean; message: string } | null;
  onNewProject: () => void;
  onDiscard: () => void;
  onRenameProject: (name: string) => void;
  onAddTarget: () => void;
  onSelectTarget: (id: string) => void;
  onRemoveTarget: (id: string) => void;
  onPatchTarget: (patch: Partial<TargetDraft>) => void;
  onSetMode: (mode: PlaceMode) => void;
  onCenterCurrent: () => void;
  onAddPlan: () => void;
  onPatchPlan: (id: string, patch: Partial<ExposurePlanDraft>) => void;
  onRemovePlan: (id: string) => void;
  onApplyPlanTemplate: (planTemplateId: string) => void;
  /** Open the create-template modal; resolves to the new template (or null). */
  onRequestNewTemplate: () => Promise<ExposureTemplate | null>;
  /** NINA default rule weights — enables the editor's "Reset to defaults". */
  ruleWeightDefaults: RuleWeight[];
  onPatchRuleWeights: (weights: RuleWeight[]) => void;
  onSave: () => void;
}

function raToHms(raDeg: number): string {
  const h = (((raDeg % 360) + 360) % 360) / 15;
  const hh = Math.floor(h);
  const mm = Math.floor((h - hh) * 60);
  const ss = ((h - hh) * 60 - mm) * 60;
  return `${hh}h${String(mm).padStart(2, "0")}m${ss.toFixed(1)}s`;
}

export default function ProjectBuilder({
  fov,
  draft,
  placeMode,
  templates,
  planTemplates,
  saving,
  editing,
  saveResult,
  onNewProject,
  onDiscard,
  onRenameProject,
  onAddTarget,
  onSelectTarget,
  onRemoveTarget,
  onPatchTarget,
  onSetMode,
  onCenterCurrent,
  onAddPlan,
  onPatchPlan,
  onRemovePlan,
  onApplyPlanTemplate,
  onRequestNewTemplate,
  ruleWeightDefaults,
  onPatchRuleWeights,
  onSave,
}: Props) {
  const hasFov = !!fov && fov.widthDeg > 0 && fov.heightDeg > 0;
  const active = draft?.targets.find((t) => t.id === draft.activeTargetId) ?? null;
  const isMosaic = active ? active.cols * active.rows > 1 : false;
  const plans = draft?.exposurePlans ?? [];
  const canSave =
    !!draft &&
    !!draft.name.trim() &&
    !!draft.profileId.trim() &&
    draft.targets.length > 0 &&
    plans.some((p) => p.exposureTemplateId != null) &&
    hasFov &&
    !saving;

  // Overall coverage span (overlap-adjusted) of the active target.
  let spanW = 0;
  let spanH = 0;
  if (active && fov) {
    const f = 1 - active.overlapPct / 100;
    spanW = (active.cols - 1) * fov.widthDeg * f + fov.widthDeg;
    spanH = (active.rows - 1) * fov.heightDeg * f + fov.heightDeg;
  }

  return (
    <details className="project-builder" open>
      <summary>
        <span className="eq-title">Project</span>
        {draft && editing && <span className="rw-edited">editing</span>}
        {draft && (
          <span className="eq-fov">
            {draft.targets.length} target{draft.targets.length === 1 ? "" : "s"}
          </span>
        )}
      </summary>

      <div className="eq-body">
        {!hasFov && (
          <div className="eq-readout warn">
            Select a rig with a valid FOV to frame targets.
          </div>
        )}

        {!draft ? (
          <>
            <button onClick={onNewProject} disabled={!hasFov}>
              ＋ New project
            </button>
            {saveResult && (
              <div className={saveResult.ok ? "eq-readout save-ok" : "eq-readout warn"}>
                {saveResult.message}
              </div>
            )}
          </>
        ) : (
          <>
            <label className="eq-field eq-name">
              Project
              <input
                value={draft.name}
                onChange={(e) => onRenameProject(e.target.value)}
              />
            </label>

            <div className="target-list">
              {draft.targets.map((t) => (
                <div
                  key={t.id}
                  className={
                    t.id === draft.activeTargetId
                      ? "target-row active"
                      : "target-row"
                  }
                  onClick={() => onSelectTarget(t.id)}
                >
                  <span className="target-name">{t.name || "(unnamed)"}</span>
                  <span className="target-panes">
                    {t.cols}×{t.rows}
                  </span>
                  <button
                    className="target-del"
                    title="Remove target"
                    onClick={(e) => {
                      e.stopPropagation();
                      onRemoveTarget(t.id);
                    }}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
            <div className="eq-row">
              <button onClick={onAddTarget} title="Add another target">
                ＋ Add target
              </button>
              <button onClick={onDiscard} title="Discard this project">
                🗑
              </button>
            </div>

            {active && (
              <>
                <hr className="pb-sep" />
                <label className="eq-field eq-name">
                  Target
                  <input
                    value={active.name}
                    onChange={(e) => onPatchTarget({ name: e.target.value })}
                  />
                </label>

                <div className="eq-row">
                  <button
                    className={placeMode === "move" ? "mo-place active" : "mo-place"}
                    onClick={() => onSetMode(placeMode === "move" ? null : "move")}
                    title="Click or drag on the sky to position this target"
                  >
                    {placeMode === "move" ? "Placing…" : "Place / move"}
                  </button>
                  <button
                    className={
                      placeMode === "coverage" ? "mo-place active" : "mo-place"
                    }
                    onClick={() =>
                      onSetMode(placeMode === "coverage" ? null : "coverage")
                    }
                    title="Drag a box over the area you want imaged; panes auto-fill to cover it"
                  >
                    {placeMode === "coverage" ? "Drag area…" : "Cover area"}
                  </button>
                </div>
                <div className="eq-row">
                  <button onClick={onCenterCurrent} title="Center on current view">
                    Center here
                  </button>
                </div>

                <div className="mo-grid">
                  <label className="eq-field">
                    Columns
                    <input
                      type="number"
                      min={1}
                      max={20}
                      value={active.cols}
                      onChange={(e) =>
                        onPatchTarget({
                          cols: Math.max(1, Math.round(Number(e.target.value))),
                        })
                      }
                    />
                  </label>
                  <label className="eq-field">
                    Rows
                    <input
                      type="number"
                      min={1}
                      max={20}
                      value={active.rows}
                      onChange={(e) =>
                        onPatchTarget({
                          rows: Math.max(1, Math.round(Number(e.target.value))),
                        })
                      }
                    />
                  </label>
                </div>

                {isMosaic && (
                  <label className="eq-field">
                    Overlap {active.overlapPct}%
                    <input
                      type="range"
                      min={0}
                      max={50}
                      step={1}
                      value={active.overlapPct}
                      onChange={(e) =>
                        onPatchTarget({ overlapPct: Number(e.target.value) })
                      }
                    />
                  </label>
                )}

                <label className="eq-field">
                  Rotation {Math.round(active.rotationDeg)}°
                  <input
                    type="range"
                    min={0}
                    max={359}
                    step={1}
                    value={active.rotationDeg}
                    onChange={(e) =>
                      onPatchTarget({ rotationDeg: Number(e.target.value) })
                    }
                  />
                </label>

                <div className="eq-readout">
                  {isMosaic ? `${active.cols * active.rows} panes · ` : "single · "}
                  {raToHms(active.centerRa)} / {active.centerDec.toFixed(3)}°
                  <br />
                  Coverage {spanW.toFixed(2)}° × {spanH.toFixed(2)}°
                </div>
              </>
            )}

            <hr className="pb-sep" />
            <div className="plan-head">
              <span className="eq-subtitle">Exposure plans</span>
              <button className="plan-add" onClick={onAddPlan} title="Add a filter">
                ＋
              </button>
            </div>
            {planTemplates.length > 0 && (
              <select
                className="plan-template-apply"
                value=""
                title="Apply a saved exposure plan template — fills the plans below in one pick"
                onChange={(e) => {
                  if (e.target.value) onApplyPlanTemplate(e.target.value);
                  e.target.value = "";
                }}
              >
                <option value="">Apply plan template…</option>
                {planTemplates.map((pt) => (
                  <option key={pt.id} value={pt.id}>
                    {pt.name} ({pt.items.length})
                  </option>
                ))}
              </select>
            )}
            <div className="plan-list">
              {templates.length === 0 && (
                <div className="eq-readout warn">
                  No exposure templates found in the database.
                </div>
              )}
              {plans.map((p) => (
                <div className="plan-row" key={p.id}>
                  <select
                    className="plan-template"
                    title="Pick the exposure template to image through"
                    value={p.exposureTemplateId != null ? String(p.exposureTemplateId) : ""}
                    onChange={async (e) => {
                      const v = e.target.value;
                      if (v === "__new__") {
                        const t = await onRequestNewTemplate();
                        if (t)
                          onPatchPlan(p.id, {
                            exposureTemplateId: t.id,
                            filterName: t.filter_name ?? t.name,
                            exposure: t.default_exposure ?? p.exposure,
                          });
                        return;
                      }
                      if (v === "") {
                        onPatchPlan(p.id, { exposureTemplateId: null });
                        return;
                      }
                      const t = templates.find((t) => t.id === Number(v));
                      onPatchPlan(p.id, {
                        exposureTemplateId: Number(v),
                        filterName: t?.filter_name ?? t?.name ?? "",
                        exposure: t?.default_exposure ?? p.exposure,
                      });
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
                  <span className="plan-unit">×</span>
                  <input
                    className="plan-num"
                    type="number"
                    min={1}
                    value={p.desired}
                    title="desired frames"
                    onChange={(e) =>
                      onPatchPlan(p.id, {
                        desired: Math.max(1, Math.round(Number(e.target.value))),
                      })
                    }
                  />
                  <button
                    className="target-del"
                    title="Remove plan"
                    onClick={() => onRemovePlan(p.id)}
                  >
                    ✕
                  </button>
                </div>
              ))}
              {!plans.length && (
                <div className="eq-readout warn">Add at least one exposure plan.</div>
              )}
            </div>

            {draft.ruleWeights.length > 0 && (
              <RuleWeightsEditor
                weights={draft.ruleWeights}
                defaults={ruleWeightDefaults}
                onChange={onPatchRuleWeights}
              />
            )}

            <button
              className="eq-save"
              disabled={!canSave}
              onClick={onSave}
              title={
                canSave
                  ? "Write this project to your Target Scheduler database (a backup is taken first)"
                  : "Needs a name, a target, and an exposure plan with a template"
              }
            >
              {saving
                ? "Saving…"
                : editing
                  ? "Save changes"
                  : "Save to database"}
            </button>
            {saveResult && (
              <div className={saveResult.ok ? "eq-readout save-ok" : "eq-readout warn"}>
                {saveResult.message}
              </div>
            )}
          </>
        )}
      </div>
    </details>
  );
}
