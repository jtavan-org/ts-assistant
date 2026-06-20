import type { ExposurePlan, Project, Target } from "../api";

interface Props {
  projects: Project[];
  selectedTargetId: number | null;
  onSelectTarget: (t: Target) => void;
}

/** Roll exposure plans up into acquisition totals. `pending` is frames still
 * needed to reach the goal by the real "done" count (accepted = passed grading). */
function planTotals(plans: ExposurePlan[]) {
  let desired = 0;
  let acquired = 0;
  let accepted = 0;
  for (const p of plans) {
    desired += p.desired;
    acquired += p.acquired;
    accepted += p.accepted;
  }
  return { desired, acquired, accepted, pending: Math.max(0, desired - accepted) };
}

/** Compact "accepted/desired" completion badge, green once the goal is met.
 * Hover shows the fuller desired/acquired/accepted/pending breakdown. */
function Completion({ plans, className }: { plans: ExposurePlan[]; className: string }) {
  const t = planTotals(plans);
  if (!t.desired) return null;
  const done = t.pending === 0;
  return (
    <span
      className={className + (done ? " complete" : "")}
      title={`desired ${t.desired} · acquired ${t.acquired} · accepted ${t.accepted} · ${t.pending} to go`}
    >
      {t.accepted}/{t.desired}
    </span>
  );
}

export default function ProjectList({
  projects,
  selectedTargetId,
  onSelectTarget,
}: Props) {
  return (
    <details className="projects-panel" open>
      <summary>
        <span className="eq-title">Projects</span>
        {projects.length > 0 && <span className="count">{projects.length}</span>}
      </summary>

      {!projects.length ? (
        <div className="panel-empty">
          No projects loaded. Drop a <code>schedulerdb.sqlite</code> into
          <code> sample_database/</code> and refresh.
        </div>
      ) : (
        <div className="project-list">
          {projects.map((p) => (
            <details key={p.id} open className="project">
              <summary>
                <span className="project-name">{p.name}</span>
                <span className={`badge state-${p.state}`}>{p.state}</span>
                {p.is_mosaic && <span className="badge mosaic">mosaic</span>}
                <Completion
                  plans={p.targets.flatMap((t) => t.exposure_plans)}
                  className="proj-prog"
                />
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
                    <Completion plans={t.exposure_plans} className="target-prog" />
                    <span className="coords">
                      {t.ra_deg.toFixed(2)}°, {t.dec_deg.toFixed(2)}°
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          ))}
        </div>
      )}
    </details>
  );
}
