// Thin client for the TS Assistant backend.

// Default to the same host the page was loaded from, on the backend port (8008).
// This makes remote access work: when you open http://<server-ip>:5173 the API
// calls go to http://<server-ip>:8008 rather than the browser's own localhost.
// Override with VITE_API_BASE if the backend lives elsewhere.
const defaultApiBase = `http://${window.location.hostname}:8008/api`;

export const API_BASE = import.meta.env.VITE_API_BASE ?? defaultApiBase;

export interface Survey {
  id: string;
  label: string;
  url_or_id: string;
  is_default: boolean;
  note: string | null;
}

export interface ExposurePlan {
  id: number;
  filter_name: string | null;
  exposure: number | null;
  desired: number;
  acquired: number;
  accepted: number;
  exposure_template_id: number | null;
}

export interface Target {
  id: number;
  name: string;
  active: boolean;
  ra_deg: number;
  dec_deg: number;
  rotation: number;
  roi: number;
  epoch: string;
  project_id: number;
  project_name: string;
  exposure_plans: ExposurePlan[];
}

export interface Project {
  id: number;
  name: string;
  description: string | null;
  profile_id: string | null;
  state: string;
  priority: number | null;
  is_mosaic: boolean;
  targets: Target[];
}

export interface ExposureTemplate {
  id: number;
  profile_id: string | null;
  name: string;
  filter_name: string | null;
  gain: number | null;
  offset: number | null;
  binning: number | null;
  readout_mode: number | null;
  twilight_level: number | null;
  moon_avoidance_enabled: boolean | null;
  moon_avoidance_separation: number | null;
  moon_avoidance_width: number | null;
  maximum_humidity: number | null;
  default_exposure: number | null;
  dither_every: number | null;
  minutes_offset: number | null;
}

export interface PlanTemplateItem {
  exposure_template_id: number;
  desired: number;
}

/** A named, reusable bundle of templates + counts (e.g. "LRGB Dark Nebula").
 * App-local — no Target Scheduler table; expands into exposure plans on apply. */
export interface PlanTemplate {
  id: string;
  name: string;
  /** NINA profile this template belongs to (bg0). null = legacy/unscoped. */
  profile_id?: string | null;
  items: PlanTemplateItem[];
}

/** A NINA profile id with its display name (alias, or a truncated GUID). */
export interface ProfileInfo {
  id: string;
  name: string;
}

/** One scoring-rule weight (qiz.3). Name matches a NINA rule. */
export interface RuleWeight {
  name: string;
  weight: number;
}

export type PlanTemplateInput = Omit<PlanTemplate, "id"> & { id?: string };

/** Body for creating an exposure template (qiz.5). Only name/filter/profile are
 * required; omitted fields take NINA defaults on the backend. */
export interface ExposureTemplateInput {
  profile_id: string;
  name: string;
  filter_name: string;
  gain?: number | null;
  offset?: number | null;
  binning?: number | null;
  readout_mode?: number | null;
  twilight_level?: number;
  moon_avoidance_enabled?: boolean;
  moon_avoidance_separation?: number;
  moon_avoidance_width?: number;
  maximum_humidity?: number | null;
  default_exposure?: number;
  moon_relax_scale?: number;
  moon_relax_max_altitude?: number;
  moon_relax_min_altitude?: number;
  moon_down_enabled?: boolean;
  dither_every?: number;
  minutes_offset?: number;
}

export interface Health {
  status: string;
  db_present: boolean;
  source_db: string | null;
  mode: "LIVE" | "STAGING";
  read_path: string | null;
  write_target: string | null;
  live_error: string | null;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export interface Equipment {
  id: string;
  name: string;
  /** NINA profile this rig belongs to (bg0). null = legacy/unscoped. */
  profile_id?: string | null;
  pixel_size_um: number;
  sensor_px_w: number;
  sensor_px_h: number;
  focal_length_mm: number;
  corrector_mag: number;
  plate_scale_arcsec_per_px: number;
  fov_width_deg: number;
  fov_height_deg: number;
}

export type EquipmentInput = Omit<
  Equipment,
  "plate_scale_arcsec_per_px" | "fov_width_deg" | "fov_height_deg"
>;

async function sendJSON<T>(
  path: string,
  method: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body == null ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

// --- export (write path) ---------------------------------------------------

export interface ExportPlanInput {
  filter_name: string | null;
  exposure: number;
  desired: number;
  exposure_template_id?: number | null;
}

export interface ExportTargetInput {
  name: string;
  ra_deg: number;
  dec_deg: number;
  rotation?: number;
  exposure_plans: ExportPlanInput[];
}

export interface ExportRequest {
  profile_id: string;
  name: string;
  description?: string | null;
  is_mosaic?: boolean;
  targets: ExportTargetInput[];
  /** Optional per-rule weight overrides (qiz.3); omitted → NINA defaults. */
  rule_weights?: RuleWeight[];
}

export interface ExportResult {
  operation_id: string;
  target_db: string;
  backup_path: string;
  project_id: number;
  target_ids: number[];
  plan_ids: number[];
  counts: Record<string, number>;
}

// POST that surfaces the backend's error `detail` (422 validation, 409 busy, ...)
// rather than just the status code, so the Save UI can show a useful message.
async function postWithDetail<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && typeof j.detail === "string") msg = j.detail;
    } catch {
      /* non-JSON body */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export const createExport = (req: ExportRequest) =>
  postWithDetail<ExportResult>("/export", req);

// Append ?profile_id= only when a profile is active, so the param stays optional.
const scoped = (path: string, profileId?: string) =>
  profileId ? `${path}?profile_id=${encodeURIComponent(profileId)}` : path;

export const fetchHealth = () => getJSON<Health>("/health");
export const fetchSurveys = () => getJSON<Survey[]>("/surveys");
export const fetchProjects = () => getJSON<Project[]>("/projects");
export const fetchProfiles = () => getJSON<ProfileInfo[]>("/profiles");
export const fetchRuleWeightDefaults = () =>
  getJSON<RuleWeight[]>("/rule-weight-defaults");
export const setProfileAlias = (id: string, name: string) =>
  sendJSON<ProfileInfo>(`/profiles/${encodeURIComponent(id)}`, "PUT", { name });
export const fetchExposureTemplates = () =>
  getJSON<ExposureTemplate[]>("/exposure-templates");
export const createExposureTemplate = (input: ExposureTemplateInput) =>
  postWithDetail<ExposureTemplate>("/exposure-templates", input);
export const fetchPlanTemplates = (profileId?: string) =>
  getJSON<PlanTemplate[]>(scoped("/plan-templates", profileId));
export const createPlanTemplate = (g: PlanTemplateInput) =>
  sendJSON<PlanTemplate>("/plan-templates", "POST", g);
export const updatePlanTemplate = (g: PlanTemplate) =>
  sendJSON<PlanTemplate>(`/plan-templates/${g.id}`, "PUT", g);
export const deletePlanTemplate = (id: string) =>
  sendJSON<{ ok: boolean }>(`/plan-templates/${id}`, "DELETE");
export const fetchEquipment = (profileId?: string) =>
  getJSON<Equipment[]>(scoped("/equipment", profileId));
export const createEquipment = (e: EquipmentInput) =>
  sendJSON<Equipment>("/equipment", "POST", e);
export const updateEquipment = (e: EquipmentInput) =>
  sendJSON<Equipment>(`/equipment/${e.id}`, "PUT", e);
export const deleteEquipment = (id: string) =>
  sendJSON<{ ok: boolean }>(`/equipment/${id}`, "DELETE");
