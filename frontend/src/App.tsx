import { useEffect, useMemo, useRef, useState } from "react";
import {
  createExport,
  createExposureTemplate,
  createPlanTemplate,
  deletePlanTemplate,
  fetchExposureTemplates,
  fetchHealth,
  fetchPlanTemplates,
  fetchProfiles,
  fetchProjects,
  fetchRuleWeightDefaults,
  fetchSurveys,
  setProfileAlias,
  updatePlanTemplate,
  type ExportTargetInput,
  type ExposureTemplate,
  type ExposureTemplateInput,
  type Health,
  type PlanTemplate,
  type PlanTemplateInput,
  type ProfileInfo,
  type Project,
  type RuleWeight,
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
import ProfilePicker from "./panels/ProfilePicker";
import EquipmentPanel from "./panels/EquipmentPanel";
import PlanTemplatesPanel from "./panels/PlanTemplatesPanel";
import NewTemplateModal from "./panels/NewTemplateModal";
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
  const [planTemplates, setPlanTemplates] = useState<PlanTemplate[]>([]);
  const [profileList, setProfileList] = useState<ProfileInfo[]>([]);
  // The user's explicit pick ("" = none yet); the *effective* active profile is
  // derived below as this-or-the-first-available, so no default-sync effect is needed.
  const [selectedProfileId, setSelectedProfileId] = useState<string>("");
  const [ruleWeightDefaults, setRuleWeightDefaults] = useState<RuleWeight[]>([]);
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
  const [templateModalOpen, setTemplateModalOpen] = useState(false);
  const templateResolverRef = useRef<((t: ExposureTemplate | null) => void) | null>(null);
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
    fetchProfiles()
      .then(setProfileList)
      .catch(() => {});
    fetchRuleWeightDefaults()
      .then(setRuleWeightDefaults)
      .catch(() => {});
  }, []);

  const survey = useMemo(
    () => surveys.find((s) => s.id === surveyId),
    [surveys, surveyId],
  );
  // All profiles to offer in the picker: those named by /api/profiles, unioned with
  // any profile id seen on a loaded project/template (a robust fallback).
  const profiles: ProfileInfo[] = useMemo(() => {
    const byId = new Map<string, ProfileInfo>();
    for (const p of profileList) byId.set(p.id, p);
    const seed = (id: string | null | undefined) => {
      if (id && !byId.has(id)) byId.set(id, { id, name: id.slice(0, 8) });
    };
    for (const p of projects) seed(p.profile_id);
    for (const t of templates) seed(t.profile_id);
    return [...byId.values()];
  }, [profileList, projects, templates]);

  // Effective active profile: the user's pick, else the first available. Derived
  // (not stored) so it tracks late-loading profiles without a sync effect.
  const activeProfileId = selectedProfileId || profiles[0]?.id || "";

  // Save a friendly alias for a profile (app-local) and reflect it in the picker.
  async function onRenameProfile(id: string, name: string) {
    const info = await setProfileAlias(id, name);
    setProfileList((prev) => {
      const rest = prev.filter((p) => p.id !== info.id);
      return [...rest, info];
    });
  }

  // Plan templates are profile-scoped server-side (app-local store), so (re)fetch
  // them whenever the active profile changes.
  useEffect(() => {
    if (!activeProfileId) return;
    fetchPlanTemplates(activeProfileId).then(setPlanTemplates).catch(() => {});
  }, [activeProfileId]);

  // Scope the DB-backed data client-side: it's already fully loaded with a
  // profile_id on every row, so switching is instant (no refetch).
  const visibleProjects = useMemo(
    () =>
      activeProfileId
        ? projects.filter((p) => p.profile_id === activeProfileId)
        : projects,
    [projects, activeProfileId],
  );
  const visibleTemplates = useMemo(
    () =>
      activeProfileId
        ? templates.filter((t) => t.profile_id === activeProfileId)
        : templates,
    [templates, activeProfileId],
  );
  const targets = useMemo(
    () => visibleProjects.flatMap((p) => p.targets),
    [visibleProjects],
  );
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
      // New projects inherit the active profile chosen in the topbar picker.
      name: "New project",
      profileId: activeProfileId,
      ruleWeights: ruleWeightDefaults.map((w) => ({ ...w })),
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

  function patchRuleWeights(ruleWeights: RuleWeight[]) {
    setProjectDraft((d) => (d ? { ...d, ruleWeights } : d));
  }

  // Exposure plan templates (qiz.6) — app-side named bundles of template+count.
  async function onCreatePlanTemplate(g: PlanTemplateInput): Promise<PlanTemplate> {
    // Stamp the active profile so it's only listed under that profile.
    const res = await createPlanTemplate({ ...g, profile_id: activeProfileId });
    setPlanTemplates((prev) => [...prev, res]);
    return res;
  }
  async function onUpdatePlanTemplate(g: PlanTemplate): Promise<PlanTemplate> {
    const res = await updatePlanTemplate(g);
    setPlanTemplates((prev) => prev.map((x) => (x.id === res.id ? res : x)));
    return res;
  }
  async function onDeletePlanTemplate(id: string): Promise<void> {
    await deletePlanTemplate(id);
    setPlanTemplates((prev) => prev.filter((x) => x.id !== id));
  }

  // Create-template modal (qiz.5), promise-based so a picker can await the result.
  function requestNewTemplate(): Promise<ExposureTemplate | null> {
    return new Promise((resolve) => {
      templateResolverRef.current = resolve;
      setTemplateModalOpen(true);
    });
  }
  async function submitNewTemplate(input: ExposureTemplateInput) {
    const created = await createExposureTemplate(input); // throws -> modal shows error
    setTemplates((prev) => [...prev, created]);
    setTemplateModalOpen(false);
    templateResolverRef.current?.(created);
    templateResolverRef.current = null;
  }
  function cancelNewTemplate() {
    setTemplateModalOpen(false);
    templateResolverRef.current?.(null);
    templateResolverRef.current = null;
  }

  // Apply a plan template: replace the draft's exposure plans with its items,
  // resolving each exposure template for its filter/exposure display.
  function applyPlanTemplate(planTemplateId: string) {
    const pt = planTemplates.find((x) => x.id === planTemplateId);
    if (!pt) return;
    setProjectDraft((d) => {
      if (!d) return d;
      const plans = pt.items.map((it) => {
        const t = templates.find((x) => x.id === it.exposure_template_id);
        return {
          id: newTargetId(),
          filterName: t?.filter_name ?? t?.name ?? "",
          exposure: t?.default_exposure ?? 120,
          desired: it.desired,
          exposureTemplateId: it.exposure_template_id,
        };
      });
      return { ...d, exposurePlans: plans };
    });
  }

  // Expand every mosaic group into per-pane targets, attach the shared exposure
  // plans, and POST to the export API. Writes go to a staging DB by default, or to
  // the live DB in production mode (backend-resolved); the frontend's tested
  // mosaicPanels is the single source of geometry.
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
      // Only send weights that differ from NINA's defaults; the backend fills the
      // rest. Omit entirely when unchanged so default projects post a clean body.
      const changedWeights = draft.ruleWeights.filter(
        (w) =>
          ruleWeightDefaults.find((d) => d.name === w.name)?.weight !== w.weight,
      );
      const res = await createExport({
        profile_id: draft.profileId.trim(),
        name,
        is_mosaic: isMosaic,
        targets: apiTargets,
        ...(changedWeights.length ? { rule_weights: changedWeights } : {}),
      });

      // Surface the just-created project in the list so the user sees it land; its
      // targets also appear on the sky. In staging mode the read path doesn't load
      // the staging DB, so this is optimistic + session-local; in live mode the next
      // reload reads it back from the real DB.
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

      const targetCount = res.counts.target ?? apiTargets.length;
      const file = res.target_db.split(/[/\\]/).pop();
      setSaveResult({
        ok: true,
        message:
          health?.mode === "LIVE"
            ? `Saved “${name}” (${targetCount} target(s)) to your live Target Scheduler database, backup taken.`
            : `Saved “${name}” (${targetCount} target(s)) to staging DB “${file}”, backup taken. Import it into NINA to use it.`,
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
        <ProfilePicker
          profiles={profiles}
          activeProfileId={activeProfileId}
          onSelect={setSelectedProfileId}
          onRename={onRenameProfile}
        />
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
              {visibleProjects.length} projects · {targets.length} targets
            </span>
          ) : (
            <span className="warn">no database loaded</span>
          )}
        </div>
        {error && <div className="error">{error}</div>}
      </header>

      {health && (
        <div
          className={
            "mode-banner " +
            (health.live_error
              ? "mode-error"
              : health.mode === "LIVE"
                ? "mode-live"
                : "mode-staging")
          }
        >
          {health.live_error
            ? `⚠ Live mode is enabled but misconfigured — ${health.live_error}`
            : health.mode === "LIVE"
              ? "● PRODUCTION — changes write directly to your live Target Scheduler database"
              : "Staging mode — changes save to a separate copy; import it into NINA to use them"}
        </div>
      )}

      <div className="body">
        <aside className="sidebar">
          <EquipmentPanel profileId={activeProfileId} onFovChange={setFovSize} />
          <ProjectBuilder
            fov={fovSize}
            draft={projectDraft}
            placeMode={placeMode}
            templates={visibleTemplates}
            planTemplates={planTemplates}
            saving={saving}
            liveMode={health?.mode === "LIVE"}
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
            onApplyPlanTemplate={applyPlanTemplate}
            onRequestNewTemplate={requestNewTemplate}
            ruleWeightDefaults={ruleWeightDefaults}
            onPatchRuleWeights={patchRuleWeights}
            onSave={saveProject}
          />
          <PlanTemplatesPanel
            templates={visibleTemplates}
            planTemplates={planTemplates}
            onCreate={onCreatePlanTemplate}
            onUpdate={onUpdatePlanTemplate}
            onDelete={onDeletePlanTemplate}
            onRequestNewTemplate={requestNewTemplate}
          />
          <ProjectList
            projects={visibleProjects}
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
      {templateModalOpen && (
        <NewTemplateModal
          templates={visibleTemplates}
          profileId={activeProfileId}
          onSubmit={submitNewTemplate}
          onClose={cancelNewTemplate}
        />
      )}
    </div>
  );
}
