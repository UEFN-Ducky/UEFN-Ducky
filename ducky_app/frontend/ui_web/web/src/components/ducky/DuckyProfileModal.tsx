import { useCallback, useEffect, useMemo, useState } from "react";
import { Modal, ModalActions } from "../Modal";
import { UnsavedChangesModal } from "../UnsavedChangesModal";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { getApi } from "../../hooks/usePanelApi";
import { BLANK_PROFILE_ID } from "../../generated/bundledAgentProfiles";
import type {
  AgentProfileDto,
  AgentProfileEditorCatalogDto,
  ChatTab,
  FolderItem,
} from "../../types/panel";
import { numberedEntryName } from "../../utils/numberedEntryName";
import { chatNamesInFolder } from "../../utils/sidebarTree";
import { DuckyProfilePicker } from "./DuckyProfilePicker";
import { useDuckyCatalog } from "./DuckyCatalogContext";
import { DuckyProfileEditorForm } from "./DuckyProfileEditorForm";
import {
  catalogDefaults,
  codingAgentFromModel,
  formToConfig,
  formToProfilePatch,
  isModelGateError,
  profileToForm,
  serializeDuckyForm,
  validateModelSelection,
  type DuckyProfileFormState,
} from "./duckyProfileForm";
import { parseFavoriteSelection } from "../../hooks/favoriteModelsCatalog";
import { PickModelModal } from "./PickModelModal";
import { requestOpenSettings } from "../../navigation/openSettingsTab";
import type { DuckyEditTarget } from "./duckyProfileTypes";

type CreateStep = "pick" | "edit";

export type { DuckyEditTarget } from "./duckyProfileTypes";

export type DuckyProfileModalMode =
  | { mode: "create"; folderId?: string; filePath?: string; folders: FolderItem[]; rootChats: FolderItem["chats"] }
  | { mode: "edit"; chat: DuckyEditTarget };

interface DuckyProfileModalProps {
  open: boolean;
  state: DuckyProfileModalMode | null;
  onClose: () => void;
  onCreated?: (chat: ChatTab) => void;
  onSaved?: () => void | Promise<void>;
  onDelete?: (chat: DuckyEditTarget) => void;
  deleteActionLabel?: string;
}

export function DuckyProfileModal({
  open,
  state,
  onClose,
  onCreated,
  onSaved,
  onDelete,
  deleteActionLabel = "Archive",
}: DuckyProfileModalProps) {
  const { allStyles, defaultStyle, normalizeStyle } = useDuckyCatalog();
  const { confirm, alert } = useConfirmModal();
  const [profiles, setProfiles] = useState<AgentProfileDto[]>([]);
  const [blankProfileId, setBlankProfileId] = useState(BLANK_PROFILE_ID);
  const [catalog, setCatalog] = useState<AgentProfileEditorCatalogDto | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState(BLANK_PROFILE_ID);
  const [createStep, setCreateStep] = useState<CreateStep>("pick");
  const [form, setForm] = useState<DuckyProfileFormState>({
    name: "",
    duckyStyle: defaultStyle,
    personality: "",
    whenToUse: "",
    disabledPacks: [],
    enabledSubskills: {},
    disabledToolIds: [],
    model: "",
    ttsVoice: "",
    ttsSpeed: 0,
    thinkingEffort: "off",
  });
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [savedSnapshot, setSavedSnapshot] = useState("");
  const [leavePrompt, setLeavePrompt] = useState(false);
  const [leaveSaving, setLeaveSaving] = useState(false);
  type ModelGateIntent = "create-chat" | "save-profile" | "save-chat";
  const [modelGate, setModelGate] = useState<{
    form: DuckyProfileFormState;
    intent: ModelGateIntent;
    title: string;
    message: string;
    profileId?: string;
    confirmLabel: string;
  } | null>(null);
  const isCreate = state?.mode === "create";
  const editChat = state?.mode === "edit" ? state.chat : null;
  const showEditor = !isCreate || createStep === "edit";
  const hasUnsavedChanges = useMemo(
    () => showEditor && !loading && !!savedSnapshot && serializeDuckyForm(form) !== savedSnapshot,
    [form, savedSnapshot, showEditor, loading],
  );
  const markClean = useCallback((next: DuckyProfileFormState) => {
    setSavedSnapshot(serializeDuckyForm(next));
  }, []);
  const refreshProfiles = useCallback(async () => {
    const api = getApi();
    if (!api?.list_agent_profiles) return [];
    const res = await api.list_agent_profiles();
    setProfiles(res.profiles ?? []);
    setBlankProfileId(res.blank_profile_id || BLANK_PROFILE_ID);
    return res.profiles ?? [];
  }, []);
  const refreshCatalog = useCallback(async () => {
    const api = getApi();
    if (!api?.get_agent_profile_editor_catalog) return null;
    const res = await api.get_agent_profile_editor_catalog();
    setCatalog(res);
    return res;
  }, []);
  const styleLabel = useCallback(
    (styleId: string) => allStyles.find((s) => s.id === styleId)?.label || styleId,
    [allStyles],
  );
  useEffect(() => {
    if (!open || !state) {
      setCreateStep("pick");
      return;
    }
    setLoading(true);
    setCreateStep(state.mode === "create" ? "pick" : "edit");
    void (async () => {
      const [, cat] = await Promise.all([refreshProfiles(), refreshCatalog()]);
      if (!cat) return;
      if (state.mode === "edit") {
        const api = getApi();
        const [mcp, skills] = await Promise.all([
          api?.get_chat_mcp_plugins ? api.get_chat_mcp_plugins(state.chat.id) : null,
          api?.get_conversation_skills ? api.get_conversation_skills(state.chat.id) : null,
        ]);
        const allToolIds = (cat.default_tool_ids ?? []).map(String);
        const effectiveTools =
          mcp?.ok && mcp.override
            ? mcp.override
            : mcp?.ok
              ? (mcp.plugins ?? []).filter((p) => p.enabled).map((p) => p.id)
              : allToolIds;
        const disabledToolIds = allToolIds.filter((id) => !effectiveTools.includes(id));
        const normStyle = normalizeStyle(state.chat.duckyStyle);
        setSelectedProfileId(BLANK_PROFILE_ID);
        const next = {
          name: state.chat.duckyName?.trim() || styleLabel(normStyle),
          duckyStyle: normStyle,
          personality: state.chat.duckyPersonality || "",
          whenToUse: "",
          disabledPacks: [...(skills?.disabled_packs || [])],
          enabledSubskills: { ...(skills?.enabled_subskills || {}) },
          disabledToolIds,
          model: "",
          ttsVoice: state.chat.ttsVoice || "",
          ttsSpeed: state.chat.ttsSpeed || 0,
          thinkingEffort: state.chat.thinkingEffort || "off",
        };
        setForm(next);
        markClean(next);
      } else {
        setSelectedProfileId(BLANK_PROFILE_ID);
        const next = { ...catalogDefaults(cat), duckyStyle: defaultStyle };
        setForm(next);
        markClean(next);
      }
      setLoading(false);
    })();
  }, [open, state, refreshProfiles, refreshCatalog, defaultStyle, normalizeStyle, styleLabel, markClean]);
  const applyProfileSelection = useCallback(
    (profileId: string, listed: AgentProfileDto[], cat: AgentProfileEditorCatalogDto) => {
      setSelectedProfileId(profileId);
      if (profileId === (blankProfileId || BLANK_PROFILE_ID)) {
        setForm((prev) => {
          const next = {
            ...catalogDefaults(cat),
            name: prev.name,
            duckyStyle: prev.duckyStyle || defaultStyle,
          };
          markClean(next);
          return next;
        });
        return;
      }
      const profile = listed.find((p) => p.id === profileId);
      if (!profile) return;
      const next = profileToForm(profile);
      setForm(next);
      markClean(next);
    },
    [blankProfileId, defaultStyle, markClean],
  );
  const openBlankEditor = () => {
    if (!catalog) return;
    setSelectedProfileId(blankProfileId || BLANK_PROFILE_ID);
    setForm((prev) => {
      const next = {
        ...catalogDefaults(catalog),
        duckyStyle: prev.duckyStyle || defaultStyle,
      };
      markClean(next);
      return next;
    });
    setCreateStep("edit");
  };
  const openProfileEditor = (profile: AgentProfileDto) => {
    if (!catalog) return;
    applyProfileSelection(profile.id, profiles, catalog);
    setCreateStep("edit");
  };
  // Chat create picker: library profiles only (saved model override included).
  // Bundled starter templates belong in Settings → New Ducky, not here.
  const selectedProfile = profiles.find((p) => p.id === selectedProfileId);
  const canDeleteProfile = !!selectedProfile && selectedProfileId !== (blankProfileId || BLANK_PROFILE_ID);
  const createChatTitle = useCallback(() => {
    if (!state || state.mode !== "create") return "New ducky";
    const siblingNames = chatNamesInFolder(state.folders, state.folderId ?? "", state.rootChats);
    return numberedEntryName("New ducky", siblingNames);
  }, [state]);
  const createFromForm = async (formState: DuckyProfileFormState, profileId?: string) => {
    const api = getApi();
    if (!api || !state || state.mode !== "create") {
      throw new Error("Panel is not ready. Try again in a moment.");
    }
    const modelError = validateModelSelection(formState.model);
    if (modelError) {
      throw new Error(modelError);
    }
    const chatTitle = createChatTitle();
    const rawPid = (profileId || selectedProfileId || "").trim();
    const pid = rawPid && rawPid !== blankProfileId && rawPid !== BLANK_PROFILE_ID ? rawPid : "";
    const config = formToConfig(formState, chatTitle, pid || undefined);
    const normFile = state.filePath?.replace(/\\/g, "/");
    const conv = await api.create_conversation(state.folderId ?? "", formState.duckyStyle, normFile, config);
    const formModel = formState.model.trim();
    const parsedForm = parseFavoriteSelection(formModel);
    onCreated?.({
      id: conv.id,
      name: conv.title || chatTitle,
      duckyStyle: conv.ducky_style || formState.duckyStyle,
      duckyName: conv.ducky_name || formState.name.trim(),
      profileId: (conv as { profile_id?: string }).profile_id || pid || undefined,
      duckyPersonality: conv.ducky_personality || formState.personality.trim(),
      filePath: conv.file_path?.replace(/\\/g, "/") || normFile,
      // Bare model id + coding agent — ChatPane keys the picker off both.
      model: conv.model || parsedForm?.modelId || formModel,
      provider: conv.provider,
      codingAgent: conv.coding_agent || codingAgentFromModel(formModel),
      thinkingEffort:
        (conv as { thinking_effort?: string }).thinking_effort ||
        formState.thinkingEffort ||
        "off",
      ttsVoice: formState.ttsVoice.trim() || undefined,
      ttsSpeed: formState.ttsSpeed || undefined,
    });
    onClose();
  };
  const openModelGate = useCallback(
    (
      formState: DuckyProfileFormState,
      intent: ModelGateIntent,
      message: string,
      profileId?: string,
    ) => {
      setModelGate({
        form: formState,
        intent,
        title: intent === "create-chat" ? "Could not create ducky" : "Could not save ducky",
        message:
          message ||
          "Pick a model for this Ducky, or set a Default Model in Settings → LLMs.",
        profileId,
        confirmLabel:
          intent === "create-chat" ? "Create ducky" : intent === "save-profile" ? "Save profile" : "Save ducky",
      });
    },
    [],
  );
  const handleProfilePick = async (profile: AgentProfileDto) => {
    if (saving) return;
    const formState = profileToForm(profile);
    setSaving(true);
    try {
      await createFromForm(formState, profile.id);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (isModelGateError(message)) {
        openModelGate(formState, "create-chat", message, profile.id);
      } else {
        await alert({ title: "Could not create ducky", message: message || "Unknown error." });
      }
    } finally {
      setSaving(false);
    }
  };
  const saveProfile = async (formState: DuckyProfileFormState = form) => {
    const api = getApi();
    if (!api) return;
    const modelError = validateModelSelection(formState.model);
    if (modelError) {
      throw new Error(modelError);
    }
    const patch = formToProfilePatch(formState);
    const profile = profiles.find((p) => p.id === selectedProfileId);
    if (profile?.kind === "bundled") {
      await api.save_agent_profile_override(profile.id, patch);
    } else if (profile?.kind === "custom") {
      await api.save_agent_profile({ ...profile, ...patch });
    } else {
      await api.save_agent_profile(patch as AgentProfileDto);
    }
    await refreshProfiles();
  };
  const handleSaveProfile = async () => {
    setSaving(true);
    try {
      await saveProfile();
      markClean(form);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (isModelGateError(message)) {
        openModelGate(form, "save-profile", message, selectedProfileId);
      } else {
        await alert({ title: "Could not save ducky", message: message || "Unknown error." });
      }
    } finally {
      setSaving(false);
    }
  };
  const saveChatConfig = async (formState: DuckyProfileFormState) => {
    const api = getApi();
    if (!api || !editChat) return;
    const modelError = validateModelSelection(formState.model);
    if (modelError) {
      throw new Error(modelError);
    }
    const config = formToConfig(formState, undefined, editChat.profileId);
    if (api.apply_ducky_config) {
      await api.apply_ducky_config(editChat.id, config);
    } else {
      await api.set_conversation_ducky_style(editChat.id, formState.duckyStyle, formState.personality.trim());
    }
    await onSaved?.();
    onClose();
  };
  const runModelGateConfirm = async (model: string) => {
    if (!modelGate) return;
    const next = { ...modelGate.form, model };
    const gate = modelGate;
    setForm(next);
    setModelGate(null);
    setSaving(true);
    try {
      if (gate.intent === "create-chat") await createFromForm(next, gate.profileId);
      else if (gate.intent === "save-profile") await saveProfile(next);
      else await saveChatConfig(next);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      await alert({ title: gate.title, message: message || "Unknown error." });
    } finally {
      setSaving(false);
    }
  };
  const openProfileSettingsFromGate = () => {
    const profileId = modelGate?.profileId;
    setModelGate(null);
    onClose();
    requestOpenSettings("Duckies", profileId ? { duckyProfileId: profileId } : undefined);
  };
  const openLlmSettingsFromGate = () => {
    setModelGate(null);
    onClose();
    requestOpenSettings("LLMs");
  };
  const handleDeleteProfile = async () => {
    if (!selectedProfile) return;
    const message =
      selectedProfile.kind === "bundled"
        ? `Remove "${selectedProfile.name}" from your library? You can still start from this template when creating a new profile.`
        : `Delete profile "${selectedProfile.name}"?`;
    if (
      !(await confirm({
        message,
        confirmLabel: selectedProfile.kind === "bundled" ? "Remove" : "Delete",
        danger: true,
      }))
    )
      return;
    const api = getApi();
    if (!api?.delete_agent_profile) return;
    await api.delete_agent_profile(selectedProfile.id);
    const listed = await refreshProfiles();
    if (catalog) applyProfileSelection(BLANK_PROFILE_ID, listed, catalog);
  };
  const handlePrimary = async (): Promise<boolean> => {
    const api = getApi();
    if (!api) return false;
    setSaving(true);
    try {
      if (isCreate && state?.mode === "create") {
        await createFromForm(form);
      } else if (editChat) {
        await saveChatConfig(form);
      }
      markClean(form);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (isModelGateError(message)) {
        openModelGate(
          form,
          isCreate ? "create-chat" : "save-chat",
          message,
          selectedProfileId !== blankProfileId ? selectedProfileId : undefined,
        );
      } else {
        await alert({
          title: isCreate ? "Could not create ducky" : "Could not save ducky",
          message: message || "Unknown error.",
        });
      }
      return false;
    } finally {
      setSaving(false);
    }
  };

  const requestClose = () => {
    if (!hasUnsavedChanges) {
      setLeavePrompt(false);
      onClose();
      return;
    }
    setLeavePrompt(true);
  };

  const handleLeaveSave = async () => {
    setLeaveSaving(true);
    try {
      if (await handlePrimary()) setLeavePrompt(false);
    } finally {
      setLeaveSaving(false);
    }
  };

  const handleLeaveDiscard = () => {
    setLeavePrompt(false);
    onClose();
  };

  const saveBtnClass = `ducky-details-save-btn${hasUnsavedChanges ? " is-unsaved" : ""}`;

  const modalTitle = isCreate ? (showEditor ? "Customize ducky" : "New ducky") : "Your ducky";
  const modalWidth = showEditor ? 680 : 520;
  const modelGateModal = modelGate ? (
    <PickModelModal
      open
      title={modelGate.title}
      message={modelGate.message}
      initialModel={modelGate.form.model}
      confirmLabel={modelGate.confirmLabel}
      onClose={() => setModelGate(null)}
      onConfirm={(model) => void runModelGateConfirm(model)}
      onOpenProfileSettings={openProfileSettingsFromGate}
      onOpenLlmSettings={openLlmSettingsFromGate}
    />
  ) : null;
  if (!open || !state) {
    return modelGateModal;
  }
  return (
    <>
    <Modal
      open={open}
      onClose={requestClose}
      title={modalTitle}
      width={modalWidth}
      hideHeader
      floatingClose
      footer={
        showEditor ? (
          <div className="ducky-profile-modal-footer">
            <div className="ducky-details-save-status" aria-live="polite">
              {hasUnsavedChanges ? "Pending changes" : "No changes"}
            </div>
            {isCreate ? (
              <div className="ducky-profile-modal-footer-secondary">
                <button type="button" className="modal-btn modal-btn-muted" onClick={() => setCreateStep("pick")}>
                  Back
                </button>
              </div>
            ) : !editChat ? (
              <div className="ducky-profile-modal-footer-secondary">
                {canDeleteProfile ? (
                  <button type="button" className="modal-btn modal-btn-danger" onClick={() => void handleDeleteProfile()}>
                    Delete profile
                  </button>
                ) : null}
                <button
                  type="button"
                  className={`modal-btn ${saveBtnClass}`}
                  disabled={saving || !hasUnsavedChanges}
                  onClick={() => void handleSaveProfile()}
                  title={hasUnsavedChanges ? "Save pending changes" : "No changes to save"}
                >
                  {hasUnsavedChanges ? "Save profile · pending" : "Save profile"}
                </button>
              </div>
            ) : null}
            <ModalActions
              cancelLabel="Cancel"
              confirmLabel={
                saving
                  ? "Saving…"
                  : isCreate
                    ? "Create ducky"
                    : hasUnsavedChanges
                      ? "Save ducky · pending"
                      : "Save ducky"
              }
              confirmDisabled={saving || loading || (!isCreate && !hasUnsavedChanges)}
              confirmClassName={saveBtnClass}
              onCancel={requestClose}
              onConfirm={() => void handlePrimary()}
            />
          </div>
        ) : undefined
      }
    >
      <div className={`ducky-editor-form ducky-profile-modal-body${showEditor ? " ducky-profile-modal-body--editor" : ""}`}>
        {loading ? (
          <div className="skill-checkbox-empty">Loading…</div>
        ) : (
          <>
            {isCreate && createStep === "pick" ? (
              <div className="ducky-profile-picker-panel">
                <div className="ducky-profile-picker-intro">
                  <h3 className="ducky-profile-picker-heading">Start a new ducky</h3>
                  <p className="ducky-profile-picker-sub">
                    Pick one of your duckies, or create a new one.
                  </p>
                </div>
                {profiles.length === 0 ? (
                  <p className="ducky-profile-picker-empty">
                    No duckies in your library yet. Use Create new, or add some in Settings → Duckies.
                  </p>
                ) : null}
                <DuckyProfilePicker
                  profiles={profiles}
                  disabled={saving}
                  onBlank={openBlankEditor}
                  onPick={(profile) => void handleProfilePick(profile)}
                  onEditProfile={openProfileEditor}
                />
              </div>
            ) : null}
            {showEditor ? (
              <DuckyProfileEditorForm
                form={form}
                setForm={setForm}
                catalog={catalog}
                editChat={editChat}
                onDeleteChat={onDelete}
                deleteActionLabel={deleteActionLabel}
              />
            ) : null}
          </>
        )}
      </div>
    </Modal>
    {leavePrompt ? (
      <UnsavedChangesModal
        message={
          <>
            This ducky has <strong>pending changes</strong>. Save before closing?
          </>
        }
        saving={leaveSaving || saving}
        onSave={() => void handleLeaveSave()}
        onDiscard={handleLeaveDiscard}
        onCancel={() => setLeavePrompt(false)}
      />
    ) : null}
    {modelGateModal}
    </>
  );
}
