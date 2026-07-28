import { useEffect, useRef, useState } from "react";

import { Icons } from "../../icons/Icons";
import { isBuiltInProfile } from "../../theme/defaultProfile";
import { isPluginProfileId } from "../../theme/appearancePluginIds";
import { useAppearance } from "../../theme/AppearanceContext";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";

function isStoredUserProfile(id: string): boolean {
  return !isBuiltInProfile(id) && !isPluginProfileId(id);
}

export function AppearanceProfileHeader() {
  const {
    profiles,
    activeProfileId,
    loadProfile,
    saveAsProfile,
    deleteProfile,
    save,
    saving,
    saveMsg,
    hasUnsavedChanges,
    discardChanges,
    canEditActiveProfile,
    guardUnsavedChanges,
  } = useAppearance();
  const { confirm } = useConfirmModal();

  const [showSaveInput, setShowSaveInput] = useState(false);
  const [newProfileName, setNewProfileName] = useState("");
  const [activeNotice, setActiveNotice] = useState<string | null>(null);
  const prevProfileIdRef = useRef<string | null>(null);
  const profilesRef = useRef(profiles);
  profilesRef.current = profiles;

  const activeProfile = profiles.find((p) => p.id === activeProfileId);
  const activeIsDeletable = activeProfile ? isStoredUserProfile(activeProfile.id) : false;

  useEffect(() => {
    if (prevProfileIdRef.current === null) {
      prevProfileIdRef.current = activeProfileId;
      return;
    }
    if (prevProfileIdRef.current === activeProfileId) return;
    prevProfileIdRef.current = activeProfileId;

    const name = profilesRef.current.find((p) => p.id === activeProfileId)?.name;
    if (!name) return;

    setActiveNotice(name);
    const timer = window.setTimeout(() => setActiveNotice(null), 2800);
    return () => window.clearTimeout(timer);
  }, [activeProfileId]);

  const handleSaveAsProfile = () => {
    const name = newProfileName.trim();
    if (!name) return;
    void saveAsProfile(name).then(() => {
      setNewProfileName("");
      setShowSaveInput(false);
    });
  };

  const handleDelete = () => {
    if (!activeProfile || !activeIsDeletable) return;
    void (async () => {
      if (
        !(await confirm({
          message: `Delete profile "${activeProfile.name}"?`,
          confirmLabel: "Delete",
          danger: true,
        }))
      )
        return;
      await deleteProfile(activeProfile.id);
    })();
  };

  const tryLoadProfile = async (id: string) => {
    if (id === activeProfileId) return;
    if (!(await guardUnsavedChanges())) return;
    await loadProfile(id);
  };

  return (
    <div className="appearance-profile-header-wrap">
      <nav className="settings-view-header-tabs no-drag" aria-label="Theme profiles">
        <div className="settings-view-header-tabs-scroll">
          {profiles.map((profile) => (
            <button
              key={profile.id}
              type="button"
              className={`settings-view-header-tab${activeProfileId === profile.id ? " is-active" : ""}`}
              onClick={() => void tryLoadProfile(profile.id)}
              disabled={saving}
              title={profile.pluginId ? `From plugin ${profile.pluginId}` : undefined}
            >
              {profile.name}
            </button>
          ))}

          <button
            type="button"
            className="settings-view-header-tab appearance-profile-add-tab"
            onClick={() => setShowSaveInput((open) => !open)}
            disabled={saving}
            title="Save as new profile"
            aria-label="Save as new profile"
            aria-expanded={showSaveInput}
          >
            <Icons.Plus />
          </button>
        </div>

        {canEditActiveProfile ? (
          <div className="settings-view-header-tabs-actions">
            {hasUnsavedChanges ? (
              <button
                type="button"
                className="settings-btn appearance-tab-discard-btn settings-view-header-action-btn settings-view-header-action-btn--icon-only"
                onClick={discardChanges}
                disabled={saving}
                title="Discard unsaved changes"
                aria-label="Discard unsaved changes"
              >
                <Icons.Refresh />
              </button>
            ) : null}
            <button
              type="button"
              className={`settings-btn appearance-tab-save-btn settings-view-header-action-btn settings-view-header-action-btn--icon-only${hasUnsavedChanges ? " is-unsaved" : ""}`}
              onClick={() => void save()}
              disabled={saving}
              title={saving ? "Saving…" : hasUnsavedChanges ? "Save unsaved changes" : "Save"}
              aria-label={saving ? "Saving…" : "Save appearance"}
            >
              <Icons.Save />
            </button>
            {activeIsDeletable ? (
              <button
                type="button"
                className="settings-btn appearance-profile-delete-btn settings-view-header-action-btn settings-view-header-action-btn--icon-only"
                onClick={handleDelete}
                disabled={saving}
                title="Delete profile"
                aria-label="Delete profile"
              >
                <Icons.Trash />
              </button>
            ) : null}
          </div>
        ) : null}
      </nav>

      {showSaveInput ? (
        <div className="appearance-profile-add-popover no-drag" role="dialog" aria-label="Save as new profile">
          <input
            type="text"
            className="appearance-profile-rename-input"
            placeholder="Profile name"
            value={newProfileName}
            onChange={(e) => setNewProfileName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSaveAsProfile();
              if (e.key === "Escape") {
                setShowSaveInput(false);
                setNewProfileName("");
              }
            }}
            autoFocus
          />
          <button
            type="button"
            className="settings-btn settings-view-header-action-btn"
            onClick={handleSaveAsProfile}
            disabled={saving || !newProfileName.trim()}
          >
            Save
          </button>
          <button
            type="button"
            className="settings-btn settings-view-header-action-btn"
            onClick={() => {
              setShowSaveInput(false);
              setNewProfileName("");
            }}
          >
            Cancel
          </button>
        </div>
      ) : null}

      {activeNotice || saveMsg ? (
        <div
          className={`appearance-profile-active-notice${saveMsg && !activeNotice && saveMsg.includes("Failed") ? " is-error" : ""}`}
          role="status"
        >
          {activeNotice ? (
            <>
              <Icons.Check />
              <span>
                <strong>{activeNotice}</strong> is active
              </span>
            </>
          ) : (
            <>
              {saveMsg.includes("Failed") ? <Icons.ErrorCircle /> : <Icons.Check />}
              <span>{saveMsg}</span>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
