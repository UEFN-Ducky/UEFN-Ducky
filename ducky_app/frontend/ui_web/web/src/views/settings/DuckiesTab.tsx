import { useCallback, useEffect, useMemo, useState } from "react";
import { AppNotice } from "../../components/AppNotice";
import { DuckyAvatar } from "../../components/ducky/DuckyAvatars";
import { DuckyProfileEditorForm } from "../../components/ducky/DuckyProfileEditorForm";
import { NewDuckyModal } from "../../components/ducky/NewDuckyModal";
import {
  BLANK_PROFILE_ID,
  catalogDefaults,
  duckyAccentClass,
  duckyTagline,
  formToProfilePatch,
  isModelGateError,
  profileToForm,
  validateModelSelection,
  type DuckyProfileFormState,
} from "../../components/ducky/duckyProfileForm";
import { PickModelModal } from "../../components/ducky/PickModelModal";
import { useDuckyCatalog } from "../../components/ducky/DuckyCatalogContext";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { useTimedMessage } from "../../hooks/useTimedMessage";
import {
  peekAgentProfilesCache,
  rememberAgentProfileCatalog,
  rememberAgentProfilesList,
} from "../../hooks/agentProfilesCache";
import { onApiReady } from "../../hooks/onApiReady";
import { installPanelPushBus, subscribePanelPush } from "../../hooks/usePanelPushBus";
import { getApi } from "../../hooks/usePanelApi";
import { Icons } from "../../icons/Icons";
import {
  emitDuckyProfileChanged,
  onDuckyProfileChanged,
} from "../../navigation/duckyProfileChanged";
import { requestOpenDuckyProfileTab } from "../../navigation/openDuckyProfileTab";
import {
  registerDuckyProfileDeepLink,
  requestOpenSettings,
  takePendingDuckyProfileId,
  takePendingNewDucky,
} from "../../navigation/openSettingsTab";
import type { SettingsNavLocation } from "../../navigation/settingsHistory";
import {
  useApplySettingsDrill,
  useRecordSettingsLocation,
  useSettingsHistoryBack,
} from "../../navigation/useSettingsHistory";
import type { AgentProfileDto, AgentProfileEditorCatalogDto } from "../../types/panel";
import { targetRef } from "../../ui-targets/registry";

const LIST_AVATAR_SIZE = 84;
const DETAIL_SLIDE_MS = 280;

function deleteProfileConfirmMessage(profile: AgentProfileDto): string {
  if (profile.kind === "bundled") {
    return `Remove "${profile.name}" from your library? You can still start from this template when creating a new profile.`;
  }
  return `Delete "${profile.name}"? This is a custom ducky — it can't be recovered once deleted.`;
}

export function DuckiesTab() {
  const { defaultStyle } = useDuckyCatalog();
  const { confirm } = useConfirmModal();
  const cached = peekAgentProfilesCache();

  const [profiles, setProfiles] = useState<AgentProfileDto[]>(() => cached?.profiles ?? []);
  const [templateProfiles, setTemplateProfiles] = useState<AgentProfileDto[]>(
    () => cached?.templateProfiles ?? [],
  );
  const [blankProfileId, setBlankProfileId] = useState(
    () => cached?.blankProfileId || BLANK_PROFILE_ID,
  );
  const [catalog, setCatalog] = useState<AgentProfileEditorCatalogDto | null>(
    () => cached?.catalog ?? null,
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [newModalOpen, setNewModalOpen] = useState(false);
  const [form, setForm] = useState<DuckyProfileFormState>({
    name: "",
    duckyStyle: defaultStyle,
    personality: "",
    whenToUse: "",
    disabledPacks: [],
    enabledSubskills: {},
    disabledToolIds: [],
    model: "",
    thinkingEffort: "off",
    ttsVoice: "",
    ttsSpeed: 0,
  });
  // Match Store: only splash when we have nothing cached from this session.
  const [loading, setLoading] = useState(() => !(cached?.profiles.length));
  const [saving, setSaving] = useState(false);
  const [statusMsg, setStatusMsg] = useTimedMessage();
  const [modelGateOpen, setModelGateOpen] = useState(false);

  const refreshProfiles = useCallback(async () => {
    const api = getApi();
    if (!api?.list_agent_profiles) return [];
    const res = await api.list_agent_profiles();
    const listed = res.profiles ?? [];
    const templates = res.template_profiles ?? [];
    const blankId = res.blank_profile_id || BLANK_PROFILE_ID;
    setProfiles(listed);
    setTemplateProfiles(templates);
    setBlankProfileId(blankId);
    rememberAgentProfilesList({
      profiles: listed,
      templateProfiles: templates,
      blankProfileId: blankId,
    });
    return listed;
  }, []);

  const refreshCatalog = useCallback(async () => {
    const api = getApi();
    if (!api?.get_agent_profile_editor_catalog) return null;
    const res = await api.get_agent_profile_editor_catalog();
    setCatalog(res);
    rememberAgentProfileCatalog(res);
    return res;
  }, []);

  // Profiles and catalog independently — a slow/partial catalog must not block the list.
  useEffect(
    () =>
      onApiReady(() => {
        const hadCache = Boolean(peekAgentProfilesCache()?.profiles.length);
        if (!hadCache) setLoading(true);
        void refreshProfiles().finally(() => setLoading(false));
        void refreshCatalog();
      }),
    [refreshCatalog, refreshProfiles],
  );

  // UEFN agent tool rows land after plugin register(); re-fetch when host signals ready.
  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type !== "uefn_plugins_changed") return;
      void refreshCatalog();
    });
  }, [refreshCatalog]);

  // Deep-link from create-flow / group invite "Create new".
  useEffect(() => {
    const openPending = () => {
      if (takePendingNewDucky()) {
        setNewModalOpen(true);
        setStatusMsg("");
        return;
      }
      const pendingId = takePendingDuckyProfileId();
      if (!pendingId) return;
      const profile = profiles.find((p) => p.id === pendingId);
      if (profile) {
        setSelectedId(profile.id);
        setForm(profileToForm(profile));
        setStatusMsg("");
      }
    };
    if (!loading) openPending();
    return registerDuckyProfileDeepLink(() => {
      if (!loading) openPending();
    });
  }, [loading, profiles, setStatusMsg]);

  // One flat library: shipped starters and user-created duckies share a grid.
  const normalizedQuery = query.trim().toLowerCase();
  const matchesQuery = useCallback(
    (p: AgentProfileDto) =>
      !normalizedQuery ||
      p.name.toLowerCase().includes(normalizedQuery) ||
      (p.when_to_use || "").toLowerCase().includes(normalizedQuery),
    [normalizedQuery],
  );
  const visibleProfiles = useMemo(
    () => profiles.filter(matchesQuery),
    [profiles, matchesQuery],
  );
  const noMatches = normalizedQuery !== "" && visibleProfiles.length === 0;

  const selectedProfile = profiles.find((p) => p.id === selectedId) ?? null;
  const isNewProfile = selectedId === blankProfileId;
  const detailOpen = selectedId !== null;
  const canDeleteProfile = !!selectedProfile && !isNewProfile;

  // Keep the detail pane mounted while it slides back off-screen, so its
  // content doesn't flash to the placeholder mid-animation.
  const [detailRendered, setDetailRendered] = useState(detailOpen);
  useEffect(() => {
    if (detailOpen) {
      setDetailRendered(true);
      return;
    }
    const timer = window.setTimeout(() => setDetailRendered(false), DETAIL_SLIDE_MS);
    return () => window.clearTimeout(timer);
  }, [detailOpen]);

  const selectProfile = useCallback((profile: AgentProfileDto) => {
    setSelectedId(profile.id);
    setForm(profileToForm(profile));
    setStatusMsg("");
  }, []);

  const closeDetail = useCallback(() => {
    setSelectedId(null);
    setStatusMsg("");
  }, []);

  // Pop-out profile tabs / other Duckies editors saved — refresh list + open detail.
  useEffect(() => {
    return onDuckyProfileChanged((ev) => {
      void (async () => {
        const listed = await refreshProfiles();
        if (ev.type === "deleted") {
          if (selectedId === ev.profileId) closeDetail();
          return;
        }
        if (selectedId === ev.profileId) {
          const profile = listed.find((p) => p.id === ev.profileId);
          if (profile) setForm(profileToForm(profile));
        }
      })();
    });
  }, [refreshProfiles, selectedId, closeDetail]);

  const duckiesNavLoc = useMemo<SettingsNavLocation>(() => {
    if (!selectedId) return { kind: "settings", tab: "Duckies", name: "Duckies" };
    const label =
      selectedId === blankProfileId
        ? "New profile"
        : profiles.find((p) => p.id === selectedId)?.name || selectedId;
    return {
      kind: "settings",
      tab: "Duckies",
      drill: { type: "duckies", profileId: selectedId },
      name: label,
    };
  }, [selectedId, blankProfileId, profiles]);
  useRecordSettingsLocation(duckiesNavLoc);

  const applyDuckiesDrill = useCallback(
    (loc: SettingsNavLocation) => {
      const profileId = loc.drill?.type === "duckies" ? loc.drill.profileId : null;
      if (!profileId) {
        closeDetail();
        return;
      }
      if (profileId === blankProfileId) {
        if (!catalog) return;
        setSelectedId(blankProfileId);
        setForm({ ...catalogDefaults(catalog), duckyStyle: defaultStyle });
        setStatusMsg("");
        return;
      }
      const profile = profiles.find((p) => p.id === profileId);
      if (profile) {
        setSelectedId(profile.id);
        setForm(profileToForm(profile));
        setStatusMsg("");
      } else {
        closeDetail();
      }
    },
    [blankProfileId, catalog, closeDetail, defaultStyle, profiles],
  );
  useApplySettingsDrill("Duckies", applyDuckiesDrill);

  const historyBack = useSettingsHistoryBack(closeDetail);

  const openNewProfile = useCallback(() => {
    if (!catalog) return;
    setNewModalOpen(true);
  }, [catalog]);

  // Both new-ducky paths land on the editor (detail pane slides in) with the
  // blank profile id, so Save creates a fresh profile rather than overwriting.
  const startBlank = useCallback(() => {
    if (!catalog) return;
    setNewModalOpen(false);
    setSelectedId(blankProfileId);
    setForm({ ...catalogDefaults(catalog), duckyStyle: defaultStyle });
    setStatusMsg("");
  }, [blankProfileId, catalog, defaultStyle]);

  const startFromTemplate = useCallback(
    (profile: AgentProfileDto) => {
      setNewModalOpen(false);
      setSelectedId(blankProfileId);
      setForm(profileToForm(profile));
      setStatusMsg("");
    },
    [blankProfileId],
  );

  const persistForm = async (formState: DuckyProfileFormState) => {
    const api = getApi();
    if (!api) return;
    const modelError = validateModelSelection(formState.model);
    if (modelError) {
      throw new Error(modelError);
    }
    const patch = formToProfilePatch(formState);
    let savedMsg: string;
    let savedId = selectedProfile?.id || "";
    if (selectedProfile?.kind === "bundled") {
      await api.save_agent_profile_override(selectedProfile.id, patch);
      savedMsg = `Saved changes to "${selectedProfile.name}"`;
    } else if (selectedProfile?.kind === "custom") {
      await api.save_agent_profile({ ...selectedProfile, ...patch });
      savedMsg = `Saved "${patch.name}"`;
    } else {
      const saved = await api.save_agent_profile(patch as AgentProfileDto);
      savedId = saved.profile.id;
      savedMsg = `Created "${saved.profile.name}"`;
    }
    if (savedId) {
      emitDuckyProfileChanged({
        type: "saved",
        profileId: savedId,
        name: patch.name || selectedProfile?.name,
        duckyStyle: patch.ducky_style || selectedProfile?.ducky_style,
      });
    }
    await refreshProfiles();
    closeDetail();
    setStatusMsg(savedMsg);
  };

  const handleSave = async () => {
    setSaving(true);
    setStatusMsg("");
    try {
      await persistForm(form);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Save failed";
      if (isModelGateError(message)) {
        setModelGateOpen(true);
      } else {
        setStatusMsg(message);
      }
    } finally {
      setSaving(false);
    }
  };

  const duplicateProfile = useCallback(
    async (profile: AgentProfileDto) => {
      const api = getApi();
      if (!api?.duplicate_agent_profile) return;
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
        await refreshProfiles();
        selectProfile(res.profile);
        setStatusMsg(`Duplicated as "${res.profile.name}"`);
      } catch (err) {
        setStatusMsg(err instanceof Error ? err.message : "Duplicate failed");
      } finally {
        setSaving(false);
      }
    },
    [refreshProfiles, selectProfile, setStatusMsg],
  );

  const deleteProfile = useCallback(
    async (profile: AgentProfileDto) => {
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
        await refreshProfiles();
        if (selectedId === profile.id) closeDetail();
        setStatusMsg(
          profile.kind === "bundled"
            ? `Removed "${profile.name}" from library`
            : `Deleted "${profile.name}"`,
        );
      } catch (err) {
        setStatusMsg(err instanceof Error ? err.message : "Delete failed");
      } finally {
        setSaving(false);
      }
    },
    [confirm, refreshProfiles, selectedId, closeDetail, setStatusMsg],
  );

  const firstProfileId = visibleProfiles[0]?.id ?? null;

  const renderCard = useCallback(
    (profile: AgentProfileDto) => {
      const active = selectedId === profile.id;
      const isCustom = profile.kind === "custom";
      const isFirst = firstProfileId === profile.id;
      return (
        <div
          key={profile.id}
          ref={
            isFirst
              ? targetRef("settings.duckies.row.first", {
                  kind: "button",
                  label: profile.name,
                  route: "settings.duckies",
                })
              : targetRef(`settings.duckies.row.${profile.id}`, {
                  kind: "button",
                  label: profile.name,
                  route: "settings.duckies",
                })
          }
          role="button"
          tabIndex={0}
          className={`ducky-card ${duckyAccentClass(profile.id)}${active ? " is-selected" : ""}`}
          onClick={() => selectProfile(profile)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              selectProfile(profile);
            }
          }}
          aria-label={profile.name}
        >
          <button
            type="button"
            className="ducky-card-duplicate"
            title="Duplicate ducky"
            aria-label={`Duplicate ${profile.name}`}
            onClick={(e) => {
              e.stopPropagation();
              void duplicateProfile(profile);
            }}
          >
            <Icons.Copy />
          </button>
          <button
            type="button"
            className="ducky-card-delete"
            title={isCustom ? "Delete ducky" : "Remove from library"}
            aria-label={`${isCustom ? "Delete" : "Remove"} ${profile.name}`}
            onClick={(e) => {
              e.stopPropagation();
              void deleteProfile(profile);
            }}
          >
            <Icons.Trash />
          </button>
          <span className="ducky-card-glow" aria-hidden />
          <span className="ducky-card-avatar-wrap">
            <DuckyAvatar styleId={profile.ducky_style} size={LIST_AVATAR_SIZE} />
          </span>
          <span className="ducky-card-meta">
            <span className="ducky-card-name">{profile.name}</span>
            <span className="ducky-card-tag">{duckyTagline(profile)}</span>
          </span>
        </div>
      );
    },
    [selectedId, selectProfile, duplicateProfile, deleteProfile, firstProfileId],
  );

  const detailTitle = isNewProfile ? "New profile" : "Editing Profile";
  const deleteLabel =
    selectedProfile?.kind === "bundled" ? "Remove" : "Delete";

  return (
    <div className="duckies-tab">
      <AppNotice message={statusMsg} className="duckies-tab-notice" />

      <div className={`duckies-tab-body${detailOpen ? " is-detail" : ""}`}>
        <div className="duckies-tab-list-shell">
          <aside className="duckies-tab-list" aria-label="Ducky profiles">
            <div className="duckies-tab-header">
              <div className="duckies-tab-header-titles">
                <h2 className="duckies-tab-title">Duckies</h2>
              </div>
              <div className="duckies-tab-header-actions">
                <div className="duckies-tab-search">
                  <span className="duckies-tab-search-icon" aria-hidden>
                    <Icons.Search />
                  </span>
                  <input
                    type="text"
                    className="duckies-tab-search-input"
                    placeholder="Search duckies…"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    aria-label="Search duckies"
                  />
                </div>
                <button
                  type="button"
                  className="duckies-tab-new-btn"
                  onClick={openNewProfile}
                  disabled={loading || !catalog}
                >
                  <Icons.Plus />
                  <span>New Ducky</span>
                </button>
              </div>
            </div>

            {loading && profiles.length === 0 ? (
              <div className="duckies-tab-list-empty">Loading…</div>
            ) : noMatches ? (
              <div className="duckies-tab-list-empty">No duckies match “{query.trim()}”.</div>
            ) : visibleProfiles.length > 0 ? (
              <div className="ducky-card-grid" role="list">
                {visibleProfiles.map(renderCard)}
              </div>
            ) : (
              <div className="duckies-tab-list-empty">
                No duckies yet — create one to get started.
              </div>
            )}
          </aside>
        </div>

        <div className="duckies-tab-detail" aria-hidden={!detailOpen}>
          {detailRendered ? (
            <div className="duckies-tab-detail-inner">
              <header className="duckies-tab-detail-head">
                <div className="duckies-tab-detail-head-left">
                  <button
                    ref={targetRef("settings.duckies.back", {
                      kind: "button",
                      label: "Back",
                      route: "settings.duckies",
                    })}
                    type="button"
                    className="duckies-tab-detail-back"
                    onClick={historyBack}
                    aria-label="Close profile details"
                  >
                    <span className="duckies-tab-detail-back-icon" aria-hidden>
                      <Icons.ChevronRight />
                    </span>
                  </button>
                  <span className="duckies-tab-detail-divider" aria-hidden />
                  <h3 className="duckies-tab-detail-title">{detailTitle}</h3>
                </div>
                <div className="duckies-tab-detail-head-actions">
                  {!isNewProfile && selectedProfile ? (
                    <button
                      type="button"
                      className="duckies-tab-detail-duplicate"
                      disabled={saving}
                      title="Open as tab"
                      aria-label="Open as tab"
                      onClick={() =>
                        requestOpenDuckyProfileTab({
                          profileId: selectedProfile.id,
                          name: selectedProfile.name,
                          duckyStyle: selectedProfile.ducky_style,
                        })
                      }
                    >
                      <Icons.Split />
                    </button>
                  ) : null}
                  {!isNewProfile && selectedProfile ? (
                    <button
                      type="button"
                      className="duckies-tab-detail-duplicate"
                      disabled={saving}
                      title="Duplicate"
                      aria-label="Duplicate"
                      onClick={() => void duplicateProfile(selectedProfile)}
                    >
                      <Icons.Copy />
                    </button>
                  ) : null}
                  {canDeleteProfile && selectedProfile ? (
                    <button
                      type="button"
                      className="duckies-tab-detail-delete"
                      disabled={saving}
                      title={deleteLabel}
                      aria-label={deleteLabel}
                      onClick={() => void deleteProfile(selectedProfile)}
                    >
                      <Icons.Trash />
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="duckies-tab-detail-save"
                    disabled={saving || !catalog}
                    title={saving ? "Saving…" : isNewProfile ? "Create Profile" : "Save Profile"}
                    aria-label={saving ? "Saving…" : isNewProfile ? "Create Profile" : "Save Profile"}
                    onClick={() => void handleSave()}
                  >
                    {isNewProfile ? <Icons.Plus /> : <Icons.Save />}
                  </button>
                </div>
              </header>

              <div className="duckies-tab-detail-scroll selectable-text">
                {catalog ? (
                  <DuckyProfileEditorForm
                    form={form}
                    setForm={setForm}
                    catalog={catalog}
                    profileId={!isNewProfile && selectedProfile ? selectedProfile.id : undefined}
                    statsDuckyName={
                      !isNewProfile && selectedProfile ? selectedProfile.name : undefined
                    }
                  />
                ) : (
                  <div className="skill-checkbox-empty">Loading editor…</div>
                )}
              </div>
            </div>
          ) : (
            <div className="duckies-tab-detail-placeholder">
              <p>Select a ducky on the left, or create a new profile.</p>
            </div>
          )}
        </div>
      </div>

      <NewDuckyModal
        open={newModalOpen}
        templates={templateProfiles}
        disabled={saving}
        onClose={() => setNewModalOpen(false)}
        onBlank={startBlank}
        onPick={startFromTemplate}
      />

      <PickModelModal
        open={modelGateOpen}
        title="Could not save ducky"
        message="Pick a model for this Ducky, or set a Default Model in Settings → LLMs."
        initialModel={form.model}
        confirmLabel={isNewProfile ? "Create Profile" : "Save Profile"}
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
