import type { Project, Target } from "../api";

interface Props {
  projects: Project[];
  selectedTargetId: number | null;
  onSelectTarget: (t: Target) => void;
}

export default function ProjectList({
  projects,
  selectedTargetId,
  onSelectTarget,
}: Props) {
  if (!projects.length) {
    return (
      <div className="panel-empty">
        No projects loaded. Drop a <code>schedulerdb.sqlite</code> into
        <code> sample_database/</code> and refresh.
      </div>
    );
  }

  return (
    <div className="project-list">
      {projects.map((p) => (
        <details key={p.id} open className="project">
          <summary>
            <span className="project-name">{p.name}</span>
            <span className={`badge state-${p.state}`}>{p.state}</span>
            {p.is_mosaic && <span className="badge mosaic">mosaic</span>}
            <span className="count">{p.targets.length}</span>
          </summary>
          <ul>
            {p.targets.map((t) => (
              <li
                key={t.id}
                className={
                  "target" + (t.id === selectedTargetId ? " selected" : "")
                }
                onClick={() => onSelectTarget(t)}
              >
                <span className={"dot" + (t.active ? " active" : "")} />
                <span className="target-name">{t.name}</span>
                <span className="coords">
                  {t.ra_deg.toFixed(2)}°, {t.dec_deg.toFixed(2)}°
                </span>
              </li>
            ))}
          </ul>
        </details>
      ))}
    </div>
  );
}
