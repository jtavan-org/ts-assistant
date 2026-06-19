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

export interface PlanGroupItem {
  exposure_template_id: number;
  desired: number;
}

/** A named, reusable bundle of templates + counts (e.g. "LRGB Dark Nebula").
 * App-local — no Target Scheduler table; expands into exposure plans on apply. */
export interface PlanGroup {
  id: string;
  name: string;
  items: PlanGroupItem[];
}

export type PlanGroupInput = Omit<PlanGroup, "id"> & { id?: string };

export interface Health {
  status: string;
  db_present: boolean;
  source_db: string | null;
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export interface Equipment {
  id: string;
  name: string;
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

export const fetchHealth = () => getJSON<Health>("/health");
export const fetchSurveys = () => getJSON<Survey[]>("/surveys");
export const fetchProjects = () => getJSON<Project[]>("/projects");
export const fetchExposureTemplates = () =>
  getJSON<ExposureTemplate[]>("/exposure-templates");
export const fetchPlanGroups = () => getJSON<PlanGroup[]>("/plan-groups");
export const createPlanGroup = (g: PlanGroupInput) =>
  sendJSON<PlanGroup>("/plan-groups", "POST", g);
export const updatePlanGroup = (g: PlanGroup) =>
  sendJSON<PlanGroup>(`/plan-groups/${g.id}`, "PUT", g);
export const deletePlanGroup = (id: string) =>
  sendJSON<{ ok: boolean }>(`/plan-groups/${id}`, "DELETE");
export const fetchEquipment = () => getJSON<Equipment[]>("/equipment");
export const createEquipment = (e: EquipmentInput) =>
  sendJSON<Equipment>("/equipment", "POST", e);
export const updateEquipment = (e: EquipmentInput) =>
  sendJSON<Equipment>(`/equipment/${e.id}`, "PUT", e);
export const deleteEquipment = (id: string) =>
  sendJSON<{ ok: boolean }>(`/equipment/${id}`, "DELETE");
