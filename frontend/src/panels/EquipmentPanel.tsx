import { useEffect, useState } from "react";
import {
  createEquipment,
  deleteEquipment,
  fetchEquipment,
  updateEquipment,
  type Equipment,
  type EquipmentInput,
} from "../api";
import { computeFov } from "../sky/fov";
import type { FovBox } from "../sky/AladinView";

interface Props {
  /** Active NINA profile — rigs are scoped to it (bg0). */
  profileId: string;
  /** Emits the selected/edited profile's field of view (degrees). */
  onFovChange: (fov: FovBox | null) => void;
}

const BLANK: Omit<EquipmentInput, "id"> = {
  name: "New rig",
  pixel_size_um: 3.76,
  sensor_px_w: 6000,
  sensor_px_h: 4000,
  focal_length_mm: 500,
  corrector_mag: 1.0,
};

type NumField =
  | "pixel_size_um"
  | "sensor_px_w"
  | "sensor_px_h"
  | "focal_length_mm"
  | "corrector_mag";

const NUM_FIELDS: { key: NumField; label: string; step: number }[] = [
  { key: "pixel_size_um", label: "Pixel size (µm)", step: 0.01 },
  { key: "focal_length_mm", label: "Focal length (mm)", step: 1 },
  { key: "corrector_mag", label: "Corrector ×", step: 0.01 },
  { key: "sensor_px_w", label: "Sensor W (px)", step: 1 },
  { key: "sensor_px_h", label: "Sensor H (px)", step: 1 },
];

export default function EquipmentPanel({ profileId, onFovChange }: Props) {
  const [profiles, setProfiles] = useState<Equipment[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [working, setWorking] = useState<EquipmentInput | null>(null);
  const [dirty, setDirty] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function emitFov(w: EquipmentInput | null) {
    if (!w) return onFovChange(null);
    const f = computeFov(
      w.pixel_size_um,
      w.sensor_px_w,
      w.sensor_px_h,
      w.focal_length_mm,
      w.corrector_mag,
    );
    onFovChange({ widthDeg: f.fovWidthDeg, heightDeg: f.fovHeightDeg });
  }

  function select(list: Equipment[], id: string) {
    const p = list.find((x) => x.id === id) ?? list[0];
    setSelectedId(p?.id ?? "");
    const w = p ? (({ ...p }) as EquipmentInput) : null;
    setWorking(w);
    setDirty(false);
    emitFov(w);
  }

  // Load rigs for the active profile; refetch when it changes.
  useEffect(() => {
    fetchEquipment(profileId || undefined)
      .then((list) => {
        setProfiles(list);
        select(list, list[0]?.id ?? "");
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId]);

  function setField(key: keyof EquipmentInput, value: string | number) {
    if (!working) return;
    const next = { ...working, [key]: value };
    setWorking(next);
    setDirty(true);
    emitFov(next);
  }

  async function save() {
    if (!working) return;
    try {
      const saved = await updateEquipment(working);
      setProfiles((ps) => ps.map((p) => (p.id === saved.id ? saved : p)));
      setDirty(false);
      setErr(null);
    } catch (e) {
      setErr(`Save failed: ${e}`);
    }
  }

  async function add() {
    try {
      const created = await createEquipment({ id: "", profile_id: profileId, ...BLANK });
      const list = [...profiles, created];
      setProfiles(list);
      select(list, created.id);
      setErr(null);
    } catch (e) {
      setErr(`Create failed: ${e}`);
    }
  }

  async function remove() {
    if (!selectedId) return;
    try {
      await deleteEquipment(selectedId);
      const list = profiles.filter((p) => p.id !== selectedId);
      setProfiles(list);
      select(list, list[0]?.id ?? "");
      setErr(null);
    } catch (e) {
      setErr(`Delete failed: ${e}`);
    }
  }

  const fov = working
    ? computeFov(
        working.pixel_size_um,
        working.sensor_px_w,
        working.sensor_px_h,
        working.focal_length_mm,
        working.corrector_mag,
      )
    : null;

  return (
    <details className="equipment" open>
      <summary>
        <span className="eq-title">Equipment</span>
        {fov && (
          <span className="eq-fov">
            {fov.fovWidthDeg.toFixed(2)}° × {fov.fovHeightDeg.toFixed(2)}°
          </span>
        )}
      </summary>

      <div className="eq-body">
        <div className="eq-row">
          <select
            value={selectedId}
            onChange={(e) => select(profiles, e.target.value)}
          >
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <button onClick={add} title="New profile">
            ＋
          </button>
          <button onClick={remove} title="Delete profile" disabled={!selectedId}>
            🗑
          </button>
        </div>

        {working && (
          <>
            <label className="eq-field eq-name">
              Name
              <input
                value={working.name}
                onChange={(e) => setField("name", e.target.value)}
              />
            </label>
            {NUM_FIELDS.map(({ key, label, step }) => (
              <label className="eq-field" key={key}>
                {label}
                <input
                  type="number"
                  step={step}
                  value={working[key] as number}
                  onChange={(e) => setField(key, Number(e.target.value))}
                />
              </label>
            ))}

            <div className="eq-readout">
              {fov && fov.plateScaleArcsecPerPx > 0 ? (
                <>
                  {fov.plateScaleArcsecPerPx.toFixed(2)}″/px ·{" "}
                  {fov.fovWidthDeg.toFixed(2)}° × {fov.fovHeightDeg.toFixed(2)}°
                </>
              ) : (
                <span className="warn">enter valid optics</span>
              )}
            </div>

            <button className="eq-save" onClick={save} disabled={!dirty}>
              {dirty ? "Save" : "Saved"}
            </button>
            {err && <div className="eq-error">{err}</div>}
          </>
        )}
      </div>
    </details>
  );
}
