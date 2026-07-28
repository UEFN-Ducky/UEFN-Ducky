import type { AgentProfileDto } from "../../types/panel";
import { Icons } from "../../icons/Icons";
import { DuckyAvatar } from "./DuckyAvatars";

const PICKER_AVATAR_SIZE = 72;

interface DuckyProfilePickerProps {
  profiles: AgentProfileDto[];
  disabled?: boolean;
  onBlank: () => void;
  onPick: (profile: AgentProfileDto) => void;
  onEditProfile: (profile: AgentProfileDto) => void;
}

export function DuckyProfilePicker({
  profiles,
  disabled = false,
  onBlank,
  onPick,
  onEditProfile,
}: DuckyProfilePickerProps) {
  return (
    <div className="ducky-profile-picker-wrap ducky-profile-picker-wrap--icons">
      <div className="ducky-profile-picker-grid ducky-profile-picker-grid--icons">
        <div className="ducky-profile-picker-icon-cell">
          <div className="ducky-profile-picker-icon-thumb">
            <button
              type="button"
              className="ducky-profile-picker-icon"
              onClick={onBlank}
              disabled={disabled}
              aria-label="Create new — custom setup"
              title="Create new — custom setup"
            >
              <span className="ducky-profile-picker-avatar ducky-profile-picker-avatar--blank" aria-hidden>
                +
              </span>
            </button>
          </div>
          <span className="ducky-profile-picker-icon-label">Create new</span>
        </div>
        {profiles.map((profile) => (
          <div key={profile.id} className="ducky-profile-picker-icon-cell">
            <div className="ducky-profile-picker-icon-thumb">
              <button
                type="button"
                className="ducky-profile-picker-edit-btn"
                aria-label={`Edit ${profile.name} profile`}
                title="Edit profile"
                disabled={disabled}
                onClick={(e) => {
                  e.stopPropagation();
                  onEditProfile(profile);
                }}
              >
                <Icons.Pencil />
              </button>
              <button
                type="button"
                className="ducky-profile-picker-icon"
                onClick={() => onPick(profile)}
                disabled={disabled}
                aria-label={profile.name}
                title={profile.name}
              >
                <DuckyAvatar styleId={profile.ducky_style} size={PICKER_AVATAR_SIZE} />
              </button>
            </div>
            <span className="ducky-profile-picker-icon-label">{profile.name}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
