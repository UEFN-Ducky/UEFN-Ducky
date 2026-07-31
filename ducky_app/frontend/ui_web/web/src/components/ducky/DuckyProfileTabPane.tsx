import { useCallback, useEffect, useRef, useState } from "react";
import { AppNotice } from "../AppNotice";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import {
  peekAgentProfilesCache,
  rememberAgentProfileCatalog,
  rememberAgentProfilesList,
} from "../../hooks/agentProfilesCache";
import { onApiReady } from "../../hooks/onApiReady";
import { installPanelPushBus, subscribePanelPush } from "../../hooks/usePanelPushBus";
import { getApi } from "../../hooks/usePanelApi";
import { useTimedMessage } from "../../hooks/useTimedMessage";
import { Icons } from "../../icons/Icons";
import {
  emitDuckyProfileChanged,
  onDuckyProfileChanged,
} from "../../navigation/duckyProfileChanged";
import { requestOpenSettings } from "../../navigation/openSettingsTab";
import type { AgentProfileDto, AgentProfileEditorCatalogDto } from "../../types/panel";
import {
  DuckyProfileEditorForm,
  DuckyProfileSectionTabs,
  type DuckyProfileSectionTab,
} from "./DuckyProfileEditorForm";
import { PickModelModal } from "./PickModelModal";
import {
  formToProfilePatch,
  isModelGateError,
  profileToForm,
  validateModelSelection,
  type DuckyProfileFormState,
} from "./duckyProfileForm";

function seedFromCache(profileId: string): {
  profile: AgentProfileDto | null;
  form: DuckyProfileFormState | null;
  catalog: AgentProfileEditorCatalogDto | null;
} {
  const cached = peekAgentProfilesCache();
  const profile = cached?.profiles.find((p) => p.id === profileId) ?? null;
  return {
    profile,
    form: profile ? profileToForm(profile) : null,
    catalog: cached?.catalog ?? null,
  };
}

function deleteProfileConfirmMessage(profile: AgentProfileDto): string {
  if (profile.kind === "bundled") {
    return `Remove "${profile.name}" from your library? You can still start from this template when creating a new profile.`;
  }
  return `Delete "${profile.name}"? This is a custom ducky — it can't be recovered once deleted.`;
}

interface DuckyProfileTabPaneProps {
  profileId: string;
  onCloseTab?: () => void;
}

export function DuckyProfileTabPane({ profileId, onCloseTab }: DuckyProfileTabPaneProps) {
  const { confirm } = useConfirmModal();
  const seeded = seedFromCache(profileId);
  const [profile, setProfile] = useState<AgentProfileDto | null>(() => seeded.profile);
  const [catalog, setCatalog] = useState<AgentProfileEditorCatalogDto | null>(() => seeded.catalog);
  const [form, setForm] = useState<DuckyProfileFormState | null>(() => seeded.form);
  const [loading, setLoading] = useState(() => !seeded.form);
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [statusMsg, setStatusMsg] = useTimedMessage();
  const [modelGateOpen, setModelGateOpen] = useState(false);
  const [gone, setGone] = useState(false);
  const [sectionTab, setSectionTab] = useState<DuckyProfileSectionTab>("profile");
  const hasContentRef = useRef(Boolean(seeded.form));
  const loadGenRef = useRef(0);

  const refreshCatalog = useCallback(async () => {
    const api = getApi();
    if (!api?.get_agent_profile_editor_catalog) return;
    try {
      const cat = await api.get_agent_profile_editor_catalog();
      setCatalog(cat);
      rememberAgentProfileCatalog(cat);
    } catch {
      /* keep prior catalog */
    }
  }, []);

  const loadProfile = useCallback(async () => {
    const api = getApi();
    if (!api?.list_agent_profiles) {
      setLoading(false);
      setLoadError("Panel API unavailable");
      return;
    }
    const gen = ++loadGenRef.current;
    // Soft refresh: keep current form visible so tab switches / saves don't flash Loading…
    if (!hasContentRef.current) setLoading(true);
    setLoadError("");
    // Catalog is independent — never block the profile form on plugin register().
    void refreshCatalog();
    try {
      const listRes = await api.list_agent_profiles();
      if (gen !== loadGenRef.current) return;
      const listed = listRes.profiles ?? [];
      rememberAgentProfilesList({
        profiles: listed,
        templateProfiles: listRes.template_profiles ?? [],
        blankProfileId: listRes.blank_profile_id,
      });
      const found = listed.find((p) => p.id === profileId) ?? null;
      if (!found) {
        setProfile(null);
        setForm(null);
        hasContentRef.current = false;
        setGone(true);
        return;
      }
      setGone(false);
      setProfile(found);
      setForm(profileToForm(found));
      hasContentRef.current = true;
    } catch (err) {
      if (gen !== loadGenRef.current) return;
      setLoadError(err instanceof Error ? err.message : "Failed to load profile");
    } finally {
      if (gen === loadGenRef.current) setLoading(false);
    }
  }, [profileId, refreshCatalog]);

  useEffect(() => {
    const next = seedFromCache(profileId);
    hasContentRef.current = Boolean(next.form);
    setProfile(next.profile);
    setForm(next.form);
    setCatalog(next.catalog);
    setGone(false);
    setLoading(!next.form);
    setLoadError("");
    setSectionTab("profile");
    return onApiReady(() => void loadProfile());
  }, [loadProfile, profileId]);

  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type !== "uefn_plugins_changed") return;
      void refreshCatalog();
    });
  }, [refreshCatalog]);

  useEffect(() => {
    return onDuckyProfileChanged((ev) => {
      if (ev.profileId !== profileId) return;
      if (ev.type === "deleted") {
        setGone(true);
        setProfile(null);
        setForm(null);
        hasContentRef.current = false;
        return;
      }
      void loadProfile();
    });
  }, [profileId, loadProfile]);

  const persistForm = async (formState: DuckyProfileFormState) => {
    const api = getApi();
    if (!api || !profile) return;
    const modelError = validateModelSelection(formState.model);
    if (modelError) throw new Error(modelError);
    const patch = formToProfilePatch(formState);
    if (profile.kind === "bundled") {
      await api.save_agent_profile_override(profile.id, patch);
    } else {
      await api.save_agent_profile({ ...profile, ...patch });
    }
    const nextName = patch.name || profile.name;
    const nextStyle = patch.ducky_style || profile.ducky_style;
    emitDuckyProfileChanged({
      type: "saved",
      profileId: profile.id,
      name: nextName,
      duckyStyle: nextStyle,
    });
    setStatusMsg(`Saved "${nextName}"`);
    await loadProfile();
  };

  const handleSave = async () => {
    if (!form) return;
    setSaving(true);
    setStatusMsg("");
    try {
      await persistForm(form);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Save failed";
      if (isModelGateError(message)) setModelGateOpen(true);
      else setStatusMsg(message);
    } finally {
      setSaving(false);
    }
  };

  const duplicateProfile = async () => {
    const api = getApi();
    if (!api?.duplicate_agent_profile || !profile) return;
    setSaving(true);
    setStatusMsg("");
    try {
      const res = await api.duplicate_agent_profile(profile.id);
      emitDuckyProfileChanged({
        type: "duplicated",
        profileId: res.profile.id,
        name: res.profile.name,
        duckyStyle: res.profile.ducky_style,
      });
      // Also nudge the source list; the new profile opens nowhere until user asks.
      emitDuckyProfileChanged({ type: "saved", profileId: profile.id });
      setStatusMsg(`Duplicated as "${res.profile.name}"`);
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Duplicate failed");
    } finally {
      setSaving(false);
    }
  };

  const deleteProfile = async () => {
    if (!profile) return;
    if (
      !(await confirm({
        message: deleteProfileConfirmMessage(profile),
        confirmLabel: profile.kind === "bundled" ? "Remove" : "Delete",
        danger: true,
      }))
    ) {
      return;
    }
    const api = getApi();
    if (!api?.delete_agent_profile) return;
    setSaving(true);
    try {
      await api.delete_agent_profile(profile.id);
      emitDuckyProfileChanged({ type: "deleted", profileId: profile.id });
      onCloseTab?.();
    } catch (err) {
      setStatusMsg(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setSaving(false);
    }
  };

  if (gone) {
    return (
      <div className="ducky-profile-tab-pane">
        <div className="ducky-profile-tab-pane-empty">
          <p>This ducky was removed from the library.</p>
          <button type="button" className="duckies-tab-detail-duplicate" onClick={onCloseTab}>
            Close tab
          </button>
        </div>
      </div>
    );
  }

  // Initial splash only — never blank an already-rendered editor on refresh.
  if ((loading && !form) || (!form && !profile && !loadError)) {
    return (
      <div className="ducky-profile-tab-pane">
        <div className="ducky-profile-tab-pane-empty">Loading…</div>
      </div>
    );
  }

  if (loadError && !form) {
    return (
      <div className="ducky-profile-tab-pane">
        <div className="ducky-profile-tab-pane-empty">
          <p>{loadError}</p>
          <button
            type="button"
            className="duckies-tab-detail-duplicate"
            onClick={() => void loadProfile()}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!form || !profile) {
    return (
      <div className="ducky-profile-tab-pane">
        <div className="ducky-profile-tab-pane-empty">Loading…</div>
      </div>
    );
  }

  const deleteLabel = profile.kind === "bundled" ? "Remove" : "Delete";

  return (
    <div className="ducky-profile-tab-pane">
      <AppNotice message={statusMsg} className="duckies-tab-notice" />
      <header className="duckies-tab-detail-head ducky-profile-tab-pane-head">
        <div className="duckies-tab-detail-head-left">
          <DuckyProfileSectionTabs value={sectionTab} onChange={setSectionTab} />
        </div>
        <div className="duckies-tab-detail-head-actions">
          <button
            type="button"
            className="duckies-tab-detail-duplicate"
            disabled={saving}
            title="Duplicate"
            aria-label="Duplicate"
            onClick={() => void duplicateProfile()}
          >
            <Icons.Copy />
          </button>
          <button
            type="button"
            className="duckies-tab-detail-delete"
            disabled={saving}
            title={deleteLabel}
            aria-label={deleteLabel}
            onClick={() => void deleteProfile()}
          >
            <Icons.Trash />
          </button>
          <button
            type="button"
            className="duckies-tab-detail-save"
            disabled={saving || !catalog}
            title={saving ? "Saving…" : "Save Profile"}
            aria-label={saving ? "Saving…" : "Save Profile"}
            onClick={() => void handleSave()}
          >
            <Icons.Save />
          </button>
        </div>
      </header>
      <div className="duckies-tab-detail-scroll selectable-text">
        {catalog ? (
          <DuckyProfileEditorForm
            form={form}
            setForm={(next) =>
              setForm((prev) => {
                if (!prev) return prev;
                return typeof next === "function" ? next(prev) : next;
              })
            }
            catalog={catalog}
            profileId={profile.id}
            statsDuckyName={profile.name}
            sectionTab={sectionTab}
            onSectionTabChange={setSectionTab}
            hideTabsBar
          />
        ) : (
          <div className="skill-checkbox-empty">Loading editor…</div>
        )}
      </div>

      <PickModelModal
        open={modelGateOpen}
        title="Could not save ducky"
        message="Pick a model for this Ducky, or set a Default Model in Settings → LLMs."
        initialModel={form.model}
        confirmLabel="Save Profile"
        showSettingsLink={false}
        onClose={() => setModelGateOpen(false)}
        onConfirm={(model) => {
          const next = { ...form, model };
          setForm(next);
          setModelGateOpen(false);
          void (async () => {
            setSaving(true);
            setStatusMsg("");
            try {
              await persistForm(next);
            } catch (err) {
              setStatusMsg(err instanceof Error ? err.message : "Save failed");
            } finally {
              setSaving(false);
            }
          })();
        }}
        onOpenLlmSettings={() => {
          setModelGateOpen(false);
          requestOpenSettings("LLMs");
        }}
      />
    </div>
  );
}
