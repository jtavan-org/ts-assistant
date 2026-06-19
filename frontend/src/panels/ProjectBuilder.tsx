import type { FovBox, PlaceMode } from "../sky/AladinView";

/** One target being framed: a single pointing (1×1) or a mosaic (N×M panes). */
export interface TargetDraft {
  id: string;
  name: string;
  centerRa: number;
  centerDec: number;
  cols: number;
  rows: number;
  overlapPct: number;
  rotationDeg: number;
}

/** A draft Project: the top-tier artifact, holding one or more targets. */
export interface ProjectDraft {
  name: string;
  targets: TargetDraft[];
  activeTargetId: string | null;
}

interface Props {
  /** Current rig FOV = the size of one pane; null until a rig is selected. */
  fov: FovBox | null;
  draft: ProjectDraft | null;
  placeMode: PlaceMode;
  onNewProject: () => void;
  onDiscard: () => void;
  onRenameProject: (name: string) => void;
  onAddTarget: () => void;
  onSelectTarget: (id: string) => void;
  onRemoveTarget: (id: string) => void;
  onPatchTarget: (patch: Partial<TargetDraft>) => void;
  onSetMode: (mode: PlaceMode) => void;
  onCenterCurrent: () => void;
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
  onNewProject,
  onDiscard,
  onRenameProject,
  onAddTarget,
  onSelectTarget,
  onRemoveTarget,
  onPatchTarget,
  onSetMode,
  onCenterCurrent,
}: Props) {
  const hasFov = !!fov && fov.widthDeg > 0 && fov.heightDeg > 0;
  const active = draft?.targets.find((t) => t.id === draft.activeTargetId) ?? null;
  const isMosaic = active ? active.cols * active.rows > 1 : false;

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
          <button onClick={onNewProject} disabled={!hasFov}>
            ＋ New project
          </button>
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
                  Rotation {active.rotationDeg}°
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

            <button
              className="eq-save"
              disabled
              title="Saving the project to the Target Scheduler database arrives in the next phase"
            >
              Save to database (next phase)
            </button>
          </>
        )}
      </div>
    </details>
  );
}
