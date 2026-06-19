import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchHealth,
  fetchProjects,
  fetchSurveys,
  type Health,
  type Project,
  type Survey,
  type Target,
} from "./api";
import AladinView, {
  type SkyFocus,
  type FovBox,
  type AladinHandle,
  type TargetRender,
  type PlaceMode,
  type CoverageCorners,
} from "./sky/AladinView";
import ProjectList from "./panels/ProjectList";
import EquipmentPanel from "./panels/EquipmentPanel";
import ProjectBuilder, {
  type ProjectDraft,
  type TargetDraft,
} from "./panels/ProjectBuilder";
import { mosaicPanels, fovTopTriangle, coverageToGrid } from "./sky/fov";
import "./App.css";

// Unique local id for a draft target. Avoids crypto.randomUUID(), which is
// undefined in a non-secure context (the app is served over http on a LAN IP,
// not https/localhost). A monotonic counter keeps it collision-free per session.
let targetSeq = 0;
function newTargetId(): string {
  targetSeq += 1;
  return `t${Date.now().toString(36)}-${targetSeq}`;
}

export default function App() {
  const [surveys, setSurveys] = useState<Survey[]>([]);
  const [surveyId, setSurveyId] = useState<string>("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [selectedTargetId, setSelectedTargetId] = useState<number | null>(null);
  const [focus, setFocus] = useState<SkyFocus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showFov, setShowFov] = useState(true);
  const [showNamed, setShowNamed] = useState(false);
  const [fovSize, setFovSize] = useState<FovBox | null>(null);
  const [projectDraft, setProjectDraft] = useState<ProjectDraft | null>(null);
  const [placeMode, setPlaceMode] = useState<PlaceMode>(null);
  const aladinRef = useRef<AladinHandle>(null);

  useEffect(() => {
    fetchSurveys()
      .then((s) => {
        setSurveys(s);
        setSurveyId(s.find((x) => x.is_default)?.id ?? s[0]?.id ?? "");
      })
      .catch((e) => setError(String(e)));
    fetchHealth().then(setHealth).catch(() => {});
    fetchProjects()
      .then(setProjects)
      .catch((e) => setError(String(e)));
  }, []);

  const survey = useMemo(
    () => surveys.find((s) => s.id === surveyId),
    [surveys, surveyId],
  );
  const targets = useMemo(() => projects.flatMap((p) => p.targets), [projects]);
  const fovBox: FovBox | null = useMemo(
    () => (showFov ? fovSize : null),
    [showFov, fovSize],
  );

  // One render entry per draft target: panel boxes + orientation marker, computed
  // from each target and the current rig FOV (pane size). Geometry is frontend-only.
  const draftRender: TargetRender[] | null = useMemo(() => {
    if (!projectDraft || !fovSize || fovSize.widthDeg <= 0) return null;
    return projectDraft.targets.map((t) => {
      const panels = mosaicPanels(
        t.centerRa,
        t.centerDec,
        fovSize.widthDeg,
        fovSize.heightDeg,
        t.cols,
        t.rows,
        t.overlapPct,
        t.rotationDeg,
      );
      const f = 1 - t.overlapPct / 100;
      const spanW = (t.cols - 1) * fovSize.widthDeg * f + fovSize.widthDeg;
      const spanH = (t.rows - 1) * fovSize.heightDeg * f + fovSize.heightDeg;
      const triangle = fovTopTriangle(
        t.centerRa,
        t.centerDec,
        spanW,
        spanH,
        t.rotationDeg,
      );
      return { panels, triangle };
    });
  }, [projectDraft, fovSize]);

  function selectTarget(t: Target) {
    setSelectedTargetId(t.id);
    setFocus({ ra: t.ra_deg, dec: t.dec_deg, fov: 3, key: Date.now() });
  }

  function makeTargetDraft(name: string): TargetDraft {
    const c = aladinRef.current?.getCenter() ?? [0, 0];
    return {
      id: newTargetId(),
      name,
      centerRa: c[0],
      centerDec: c[1],
      cols: 1,
      rows: 1,
      overlapPct: 10,
      rotationDeg: 0,
    };
  }

  function newProject() {
    const t = makeTargetDraft("Target 1");
    setProjectDraft({ name: "New project", targets: [t], activeTargetId: t.id });
    setPlaceMode("move");
  }

  function addTarget() {
    setProjectDraft((d) => {
      if (!d) return d;
      const t = makeTargetDraft(`Target ${d.targets.length + 1}`);
      return { ...d, targets: [...d.targets, t], activeTargetId: t.id };
    });
    setPlaceMode("move");
  }

  function removeTarget(id: string) {
    setProjectDraft((d) => {
      if (!d) return d;
      const targets = d.targets.filter((t) => t.id !== id);
      if (!targets.length) return null; // discarded the last target
      const activeTargetId =
        d.activeTargetId === id ? targets[0].id : d.activeTargetId;
      return { ...d, targets, activeTargetId };
    });
  }

  // Patch the active target of the current project draft.
  function patchTarget(patch: Partial<TargetDraft>) {
    setProjectDraft((d) =>
      d
        ? {
            ...d,
            targets: d.targets.map((t) =>
              t.id === d.activeTargetId ? { ...t, ...patch } : t,
            ),
          }
        : d,
    );
  }

  function centerTargetHere() {
    const c = aladinRef.current?.getCenter();
    if (c) patchTarget({ centerRa: c[0], centerDec: c[1] });
  }

  // Coverage drag: auto-divide the dragged Area-of-Interest into rig-FOV panes
  // that fully cover it, adopting the AoI's center, size and orientation.
  function applyCoverage(c: CoverageCorners) {
    if (!fovSize || fovSize.widthDeg <= 0 || !projectDraft) return;
    const active = projectDraft.targets.find(
      (t) => t.id === projectDraft.activeTargetId,
    );
    const g = coverageToGrid(
      c.tl,
      c.tr,
      c.bl,
      c.br,
      fovSize.widthDeg,
      fovSize.heightDeg,
      active?.overlapPct ?? 10,
    );
    patchTarget({
      centerRa: g.centerRa,
      centerDec: g.centerDec,
      cols: g.cols,
      rows: g.rows,
      rotationDeg: g.rotationDeg,
    });
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">TS&nbsp;Assistant</div>
        <label className="survey-picker">
          Survey
          <select value={surveyId} onChange={(e) => setSurveyId(e.target.value)}>
            {surveys.map((s) => (
              <option key={s.id} value={s.id} title={s.note ?? undefined}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="fov-toggle">
          <input
            type="checkbox"
            checked={showFov}
            onChange={(e) => setShowFov(e.target.checked)}
          />
          FOV boxes
        </label>
        <label className="fov-toggle named-toggle">
          <input
            type="checkbox"
            checked={showNamed}
            onChange={(e) => setShowNamed(e.target.checked)}
          />
          Named objects
        </label>
        <div className="status">
          {health?.db_present ? (
            <span>
              {projects.length} projects · {targets.length} targets
            </span>
          ) : (
            <span className="warn">no database loaded</span>
          )}
        </div>
        {error && <div className="error">{error}</div>}
      </header>

      <div className="body">
        <aside className="sidebar">
          <EquipmentPanel onFovChange={setFovSize} />
          <ProjectBuilder
            fov={fovSize}
            draft={projectDraft}
            placeMode={placeMode}
            onNewProject={newProject}
            onDiscard={() => {
              setProjectDraft(null);
              setPlaceMode(null);
            }}
            onRenameProject={(name) =>
              setProjectDraft((d) => (d ? { ...d, name } : d))
            }
            onAddTarget={addTarget}
            onSelectTarget={(id) =>
              setProjectDraft((d) => (d ? { ...d, activeTargetId: id } : d))
            }
            onRemoveTarget={removeTarget}
            onPatchTarget={patchTarget}
            onSetMode={setPlaceMode}
            onCenterCurrent={centerTargetHere}
          />
          <ProjectList
            projects={projects}
            selectedTargetId={selectedTargetId}
            onSelectTarget={selectTarget}
          />
        </aside>
        <main className="sky">
          <AladinView
            ref={aladinRef}
            survey={survey}
            targets={targets}
            focus={focus}
            fov={fovBox}
            draft={draftRender}
            showNamedObjects={showNamed}
            placeMode={placeMode}
            onPlaceCenter={(ra, dec) =>
              patchTarget({ centerRa: ra, centerDec: dec })
            }
            onCoverageDrag={applyCoverage}
            onTargetClick={(id) => {
              const t = targets.find((x) => x.id === id);
              if (t) selectTarget(t);
            }}
          />
        </main>
      </div>
    </div>
  );
}
