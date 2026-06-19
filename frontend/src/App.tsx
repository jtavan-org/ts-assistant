import { useEffect, useMemo, useRef, useState } from "react";
import {
  createExport,
  fetchExposureTemplates,
  fetchHealth,
  fetchProjects,
  fetchSurveys,
  type ExportTargetInput,
  type ExposureTemplate,
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
  const [templates, setTemplates] = useState<ExposureTemplate[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [selectedTargetId, setSelectedTargetId] = useState<number | null>(null);
  const [focus, setFocus] = useState<SkyFocus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showFov, setShowFov] = useState(true);
  const [showNamed, setShowNamed] = useState(false);
  const [fovSize, setFovSize] = useState<FovBox | null>(null);
  const [projectDraft, setProjectDraft] = useState<ProjectDraft | null>(null);
  const [placeMode, setPlaceMode] = useState<PlaceMode>(null);
  const [saving, setSaving] = useState(false);
  const [saveResult, setSaveResult] = useState<{ ok: boolean; message: string } | null>(
    null,
  );
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
    fetchExposureTemplates()
      .then(setTemplates)
      .catch(() => {});
  }, []);

  const survey = useMemo(
    () => surveys.find((s) => s.id === surveyId),
    [surveys, surveyId],
  );
  const targets = useMemo(() => projects.flatMap((p) => p.targets), [projects]);
  // Profile ids already in the DB — a new project usually targets the same one.
  const profiles = useMemo(() => {
    const ids = new Set<string>();
    for (const p of projects) if (p.profile_id) ids.add(p.profile_id);
    return [...ids];
  }, [projects]);
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
    setProjectDraft({
      // Profile is hidden in the UI; derive it from the existing projects, or
      // fall back to a template's profile (everything is one profile for now).
      name: "New project",
      profileId: profiles[0] ?? templates[0]?.profile_id ?? "",
      targets: [t],
      activeTargetId: t.id,
      exposurePlans: [
        {
          id: newTargetId(),
          filterName: "",
          exposure: 120,
          desired: 20,
          exposureTemplateId: null,
        },
      ],
    });
    setPlaceMode("move");
    setSaveResult(null);
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

  function addPlan() {
    setProjectDraft((d) =>
      d
        ? {
            ...d,
            exposurePlans: [
              ...d.exposurePlans,
              {
                id: newTargetId(),
                filterName: "",
                exposure: 120,
                desired: 20,
                exposureTemplateId: null,
              },
            ],
          }
        : d,
    );
  }

  function patchPlan(
    id: string,
    patch: Partial<{
      filterName: string;
      exposure: number;
      desired: number;
      exposureTemplateId: number | null;
    }>,
  ) {
    setProjectDraft((d) =>
      d
        ? {
            ...d,
            exposurePlans: d.exposurePlans.map((p) =>
              p.id === id ? { ...p, ...patch } : p,
            ),
          }
        : d,
    );
  }

  function removePlan(id: string) {
    setProjectDraft((d) =>
      d ? { ...d, exposurePlans: d.exposurePlans.filter((p) => p.id !== id) } : d,
    );
  }

  // Expand every mosaic group into per-pane targets, attach the shared exposure
  // plans, and POST to the export API. Writes go to a staging DB (never the live
  // source); the frontend's tested mosaicPanels is the single source of geometry.
  async function saveProject() {
    if (!projectDraft || !fovSize || fovSize.widthDeg <= 0) return;
    const draft = projectDraft;
    const plans = draft.exposurePlans
      .filter((p) => p.exposureTemplateId != null)
      .map((p) => ({
        filter_name: p.filterName.trim() || null,
        exposure: p.exposure,
        desired: p.desired,
        exposure_template_id: p.exposureTemplateId,
      }));
    if (!plans.length) {
      setSaveResult({ ok: false, message: "Select an exposure template for at least one plan." });
      return;
    }

    const apiTargets: ExportTargetInput[] = [];
    for (const t of draft.targets) {
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
      const mosaic = t.cols * t.rows > 1;
      for (const p of panels) {
        apiTargets.push({
          name: mosaic ? `${t.name} ${p.row + 1}-${p.col + 1}` : t.name,
          ra_deg: p.centerRa,
          dec_deg: p.centerDec,
          rotation: t.rotationDeg,
          exposure_plans: plans,
        });
      }
    }

    const isMosaic = draft.targets.some((t) => t.cols * t.rows > 1);
    const name = draft.name.trim();
    setSaving(true);
    setSaveResult(null);
    try {
      const res = await createExport({
        profile_id: draft.profileId.trim(),
        name,
        is_mosaic: isMosaic,
        targets: apiTargets,
      });

      // Surface the just-created project in the list (it lives in the staging DB,
      // which the read path doesn't load) so the user sees it land; its targets
      // also appear on the sky. Optimistic + local to this session.
      const created: Project = {
        id: res.project_id,
        name,
        description: null,
        profile_id: draft.profileId.trim(),
        state: "draft",
        priority: 1,
        is_mosaic: isMosaic,
        targets: apiTargets.map((t, i) => ({
          id: res.target_ids[i] ?? -(i + 1),
          name: t.name,
          active: true,
          ra_deg: t.ra_deg,
          dec_deg: t.dec_deg,
          rotation: t.rotation ?? 0,
          roi: 100,
          epoch: "J2000",
          project_id: res.project_id,
          project_name: name,
          exposure_plans: t.exposure_plans.map((p, j) => ({
            id: -(i * 1000 + j + 1),
            filter_name: p.filter_name,
            exposure: p.exposure,
            desired: p.desired,
            acquired: 0,
            accepted: 0,
            exposure_template_id: p.exposure_template_id ?? null,
          })),
        })),
      };
      setProjects((prev) => [created, ...prev]);

      const file = res.target_db.split(/[/\\]/).pop();
      setSaveResult({
        ok: true,
        message: `Saved “${name}” (${res.counts.target ?? apiTargets.length} target(s)) to staging DB “${file}”, backup taken. Import it into NINA to use it.`,
      });
      // Return to the New-project state so another can be started; keep the message.
      setProjectDraft(null);
      setPlaceMode(null);
    } catch (e) {
      setSaveResult({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
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
            templates={templates}
            saving={saving}
            saveResult={saveResult}
            onNewProject={newProject}
            onDiscard={() => {
              setProjectDraft(null);
              setPlaceMode(null);
              setSaveResult(null);
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
            onAddPlan={addPlan}
            onPatchPlan={patchPlan}
            onRemovePlan={removePlan}
            onSave={saveProject}
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
