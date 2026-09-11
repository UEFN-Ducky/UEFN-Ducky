import type { AgentProfileDto } from "../../types/panel";
import { Icons } from "../../icons/Icons";
import { DuckyAvatar } from "./DuckyAvatars";
import type { DuckyPickerIssue } from "./duckyPickerIssue";

const PICKER_AVATAR_SIZE = 72;

interface DuckyProfilePickerProps {
  profiles: AgentProfileDto[];
  /** Informational only — tiles stay clickable. */
  issue?: DuckyPickerIssue | null;
  creating?: boolean;
  onIssueAction?: () => void;
  onBlank: () => void;
  onPick: (profile: AgentProfileDto) => void;
  onEditProfile: (profile: AgentProfileDto) => void;
}

export function DuckyProfilePicker({
  profiles,
  issue = null,
  creating = false,
  onIssueAction,
  onBlank,
  onPick,
  onEditProfile,
}: DuckyProfilePickerProps) {
  return (
    <div className="ducky-profile-picker-wrap ducky-profile-picker-wrap--icons">
      {issue ? (
        <div className="ducky-profile-picker-issue" role="status">
          <p className="ducky-profile-picker-issue-text">{issue.message}</p>
          {onIssueAction ? (
            <button type="button" className="ducky-profile-picker-issue-btn" onClick={onIssueAction}>
              {issue.actionLabel}
            </button>
          ) : null}
        </div>
      ) : null}
      {creating ? (
        <p className="ducky-profile-picker-creating" aria-live="polite">
          Creating…
        </p>
      ) : null}
      <div className="ducky-profile-picker-grid ducky-profile-picker-grid--icons">
        <div className="ducky-profile-picker-icon-cell">
          <div className="ducky-profile-picker-icon-thumb">
            <button
              type="button"
              className="ducky-profile-picker-icon"
              onClick={onBlank}
              disabled={creating}
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
                disabled={creating}
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
                disabled={creating}
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
