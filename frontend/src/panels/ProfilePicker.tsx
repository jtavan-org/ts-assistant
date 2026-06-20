import type { ProfileInfo } from "../api";

interface Props {
  profiles: ProfileInfo[];
  activeProfileId: string;
  onSelect: (id: string) => void;
}

/**
 * Top-level NINA profile picker (bg0). The active profile scopes the projects,
 * exposure templates, plan templates, and equipment shown beneath it. Always
 * visible so the organizing axis is explicit, even with a single profile.
 */
export default function ProfilePicker({ profiles, activeProfileId, onSelect }: Props) {
  return (
    <label className="survey-picker profile-picker">
      Profile
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
    </label>
  );
}
