import { useState } from "react";
import type { ProfileInfo } from "../api";

interface Props {
  profiles: ProfileInfo[];
  activeProfileId: string;
  onSelect: (id: string) => void;
  /** Persist a friendly alias for a profile (bg0 Stage 3). */
  onRename: (id: string, name: string) => Promise<void>;
}

/**
 * Top-level NINA profile picker (bg0). The active profile scopes the projects,
 * exposure templates, plan templates, and equipment shown beneath it. Always
 * visible so the organizing axis is explicit, even with a single profile.
 *
 * The scheduler DB stores only profile GUIDs, so the picker shows a short GUID by
 * default; the ✎ button lets the user attach a friendly alias (app-local).
 */
export default function ProfilePicker({
  profiles,
  activeProfileId,
  onSelect,
  onRename,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const active = profiles.find((p) => p.id === activeProfileId);

  function startEdit() {
    if (!active) return;
    setDraft(active.name);
    setEditing(true);
  }

  async function commit() {
    const name = draft.trim();
    if (name && active && name !== active.name) {
      await onRename(active.id, name);
    }
    setEditing(false);
  }

  if (editing && active) {
    return (
      <label className="survey-picker profile-picker">
        NINA Profile
        <span className="profile-rename">
          <input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void commit();
              if (e.key === "Escape") setEditing(false);
            }}
            title={active.id}
          />
          <button type="button" onClick={() => void commit()} title="Save name">
            ✓
          </button>
          <button type="button" onClick={() => setEditing(false)} title="Cancel">
            ✕
          </button>
        </span>
      </label>
    );
  }

  return (
    <label className="survey-picker profile-picker">
      NINA Profile
      <span className="profile-select">
        <select
          value={activeProfileId}
          onChange={(e) => onSelect(e.target.value)}
          disabled={profiles.length === 0}
          title="Active NINA profile — scopes projects, templates and equipment"
        >
          {profiles.length === 0 && <option value="">(no profiles)</option>}
          {profiles.map((p) => (
            <option key={p.id} value={p.id} title={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="profile-edit"
          onClick={startEdit}
          disabled={!active}
          title="Rename this profile"
        >
          ✎
        </button>
      </span>
    </label>
  );
}
