import { useEffect, useMemo, useState } from "react";
import {
  fetchHealth,
  fetchProjects,
  fetchSurveys,
  type Health,
  type Project,
  type Survey,
  type Target,
} from "./api";
import AladinView, { type SkyFocus, type FovBox } from "./sky/AladinView";
import ProjectList from "./panels/ProjectList";
import EquipmentPanel from "./panels/EquipmentPanel";
import "./App.css";

export default function App() {
  const [surveys, setSurveys] = useState<Survey[]>([]);
  const [surveyId, setSurveyId] = useState<string>("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [selectedTargetId, setSelectedTargetId] = useState<number | null>(null);
  const [focus, setFocus] = useState<SkyFocus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showFov, setShowFov] = useState(true);
  const [fovSize, setFovSize] = useState<FovBox | null>(null);

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

  function selectTarget(t: Target) {
    setSelectedTargetId(t.id);
    setFocus({ ra: t.ra_deg, dec: t.dec_deg, fov: 3, key: Date.now() });
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
          <ProjectList
            projects={projects}
            selectedTargetId={selectedTargetId}
            onSelectTarget={selectTarget}
          />
        </aside>
        <main className="sky">
          <AladinView
            survey={survey}
            targets={targets}
            focus={focus}
            fov={fovBox}
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
