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

/** One step of an override exposure order (awh). action 0 = expose the plan at
 * reference_idx; action 1 = a Dither step (reference_idx = -1). */
export interface OverrideStep {
  action: number;
  reference_idx: number;
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
  /** Override exposure order steps (awh), in order; empty = NINA's default. */
  override_exposure_order?: OverrideStep[];
}

/** Advanced project settings (psq) — NINA's project-tab knobs. */
export interface ProjectSettings {
  priority: number; // 0 Low, 1 Normal, 2 High
  minimum_time: number; // minutes
  minimum_altitude: number; // degrees
  maximum_altitude: number; // degrees (0 = none)
  use_custom_horizon: boolean;
  horizon_offset: number; // degrees
  meridian_window: number; // minutes
  filter_switch_frequency: number;
  dither_every: number;
  enable_grader: boolean;
  flats_handling: number;
  smart_exposure_order: boolean;
}

/** NINA's project defaults (mirrors writer.ProjectSpec defaults). */
export const PROJECT_SETTING_DEFAULTS: ProjectSettings = {
  priority: 1,
  minimum_time: 30,
  minimum_altitude: 0,
  maximum_altitude: 0,
  use_custom_horizon: false,
  horizon_offset: 0,
  meridian_window: 0,
  filter_switch_frequency: 0,
  dither_every: 0,
  enable_grader: true,
  flats_handling: 0,
  smart_exposure_order: false,
};

export interface Project {
  id: number;
  name: string;
  description: string | null;
  profile_id: string | null;
  state: string;
  priority: number | null;
  is_mosaic: boolean;
  targets: Target[];
  rule_weights?: RuleWeight[];
  // Advanced settings (psq); present from the reader, nullable for older rows.
  minimum_time?: number | null;
  minimum_altitude?: number | null;
  maximum_altitude?: number | null;
  use_custom_horizon?: boolean | null;
  horizon_offset?: number | null;
  meridian_window?: number | null;
  filter_switch_frequency?: number | null;
  dither_every?: number | null;
  enable_grader?: boolean | null;
  flats_handling?: number | null;
  smart_exposure_order?: boolean | null;
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
  db_path: string | null;
  backup_dir: string | null;
  error: string | null;
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
  /** Existing DB target id when editing (o2c); omitted for new targets. */
  id?: number;
  name: string;
  ra_deg: number;
  dec_deg: number;
  rotation?: number;
  exposure_plans: ExportPlanInput[];
}

export interface ExportRequest extends Partial<ProjectSettings> {
  profile_id: string;
  name: string;
  description?: string | null;
  is_mosaic?: boolean;
  targets: ExportTargetInput[];
  /** Optional per-rule weight overrides (qiz.3); omitted → NINA defaults. */
  rule_weights?: RuleWeight[];
  /** Override exposure order (awh); omitted/empty → NINA's default cadence. */
  override_exposure_order?: OverrideStep[];
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

// PUT variant of postWithDetail, surfacing the backend's error `detail` (409/422/…).
async function putWithDetail<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "PUT",
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
export const updateExport = (projectId: number, req: ExportRequest) =>
  putWithDetail<ExportResult>(`/export/${projectId}`, req);

export interface DeleteResult {
  project_id: number;
  target_db: string;
  backup_path: string;
  deleted: Record<string, number>;
}

// DELETE that surfaces the backend's error `detail` (409 busy / not-editable).
export async function deleteProject(projectId: number): Promise<DeleteResult> {
  const res = await fetch(`${API_BASE}/export/${projectId}`, { method: "DELETE" });
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
  return res.json() as Promise<DeleteResult>;
}

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
