import { createPortal } from "react-dom";
import type { AgentProfileDto } from "../../types/panel";
import { DuckyAvatar } from "./DuckyAvatars";

const POPUP_AVATAR_SIZE = 72;

interface DuckyProfileQuickPopupProps {
  profile: AgentProfileDto;
  creating?: boolean;
  onCreate: () => void;
  onClose: () => void;
}

export function DuckyProfileQuickPopup({ profile, creating, onCreate, onClose }: DuckyProfileQuickPopupProps) {
  const personality = (profile.ducky_personality || "").trim();

  return createPortal(
    <div
      className="ducky-profile-quick-popup-backdrop no-drag"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="ducky-profile-quick-popup" role="dialog" aria-label={profile.name}>
        <button type="button" className="icon-btn ducky-profile-quick-popup-close" onClick={onClose} aria-label="Close">
          <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" fill="none" strokeWidth="2.5">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
        <DuckyAvatar styleId={profile.ducky_style} size={POPUP_AVATAR_SIZE} />
        <div className="ducky-profile-quick-popup-name">{profile.name}</div>
        {personality ? (
          <p className="ducky-profile-quick-popup-personality selectable-text">{personality}</p>
        ) : (
          <p className="ducky-profile-quick-popup-personality ducky-profile-quick-popup-personality--empty">
            No personality text
          </p>
        )}
        <button
          type="button"
          className="settings-btn modal-confirm-btn ducky-profile-quick-popup-create"
          disabled={creating}
          onClick={onCreate}
        >
          {creating ? "Creating…" : "Create ducky"}
        </button>
      </div>
    </div>,
    document.body,
  );
}
