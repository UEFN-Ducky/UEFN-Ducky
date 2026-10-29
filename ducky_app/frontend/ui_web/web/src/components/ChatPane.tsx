import { subscribeLatestChatActivity } from "../navigation/latestChatActivity";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { ScopedCss, useScopedClass } from "../utils/scopedCss";
import { skipAutoFocus } from "../hooks/useNarrowLayout";
import { isDucktactoeChat } from "../plugin-ui/ducktactoeBoardChat";
import { useSwarmCardSend } from "./tool-presenters/SwarmCard";
import { usePluginContributions } from "../hooks/usePluginContributions";
import type { VirtualChatMessageListHandle } from "./VirtualChatMessageList";
import { useComposerAttachments } from "../hooks/useComposerAttachments";
import type {
  AgentMode,
  ChatTab,
  ContextUsage,
  FolderItem,
  MessageAttachmentDto,
  SessionFile,
} from "../types/panel";
import { getApi } from "../hooks/usePanelApi";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { useFocusWindow } from "../hooks/useFocusWindow";
import { useLinkedAgents } from "../hooks/useLinkedAgents";
import { useChatMessages } from "../hooks/useChatMessages";
import { useChatTurnTimerState } from "../hooks/useChatTurnTimer";
import { useAgentEventSubscription } from "../hooks/useAgentEventBus";
import { useRunningAgents } from "../hooks/useRunningAgents";
import { runningTeamChats } from "./team/teamRoster";
import { subPlanProgress, type WorkRow } from "./WorkProgressBar";
import { setRoster, useRoster } from "./team/rosterStore";
import { useHasApiKey } from "../hooks/useHasApiKey";
import { getCachedChatComposer, setCachedChatComposer, subscribeComposerDraft, takeComposerDraft } from "../hooks/chatComposerCache";
import { composerHistoryKey, recordComposer, redoComposer, sameAttachments, undoComposer } from "../hooks/composerHistory";
import { nextComposerFromChat } from "../hooks/chatComposerHydrate";
import {
  getPromptQueue,
  movePromptToFront,
  removePrompt,
  setPromptQueue,
  subscribePromptQueue,
  takeNextPromptForDrain,
  releasePromptDrainLock,
  takePromptForEdit,
  type QueuedPrompt,
} from "../hooks/promptQueue";
import { PromptQueueBar } from "./PromptQueueBar";
import { basename } from "../verse-editor/utils/isVerseFile";
import { ChatWorkflowRunCard } from "./ChatWorkflowRunCard";
import {
  getAskUserSessionForConv,
  setFocusedChatForAsk,
  subscribeAskUser,
  type AskUserSession,
} from "../ask-user";
import { useHasTerminalApprovals } from "../terminal/TerminalApprovalCard";
import { useChatColumnWidth } from "../hooks/useChatColumnWidth";
import { isModelsCatalogReady, getCachedModels, subscribeModelsCatalog } from "../hooks/modelsCatalogCache";
import { getCachedCodingAgents, subscribeCodingAgents } from "../hooks/codingAgentsCache";
import { hasUsableModels } from "./ducky/duckyPickerIssue";
import { parseFavoriteSelection } from "../hooks/favoriteModelsCatalog";
import {
  getWalkthroughState,
  isCompleted,
  startTour,
} from "../walkthrough/WalkthroughService";
import { whenWalkthroughHydrated } from "../walkthrough/persistence";
import { whenFirstRunSetupDone } from "../walkthrough/firstRunSetup";
import { isAutoTranslateChat } from "../navigation/tabTranslatePrefs";
import { usePluginUiPrefs } from "../hooks/usePluginUiPrefs";
import { requestChatTranslateWalk } from "../navigation/openTranslatedChat";
import { readTranslationUiLang } from "../navigation/openVerseTranslatedTab";
import { isEnglishLang } from "../views/settings/translationLanguages";
import type { LiveVoiceUiHandlers } from "../voice/VoiceControls";
import { noteUserTurn } from "../voice/spokenReplyGate";
import { useLedgerOpen } from "./ChatChangesDrawer";
import { GroupMemberStrip } from "./GroupMemberStrip";
import type { GroupMemberDto } from "../types/panel";
import {
  CHAT_ATTACH_DROP_ATTR,
  CHAT_ATTACH_TARGET,
  dragHasOsFiles,
} from "../utils/osFileDrag";
import { ChatComposer, composerSend, useSlashCommands } from "./ChatComposer";
import { ChatRows, useChatRowModel } from "./ChatRows";
import {
  ChatPlanCard,
  applyPlanToolDone,
  useChatPlan,
  useChatPlanActions,
  usePlanAllDone,
} from "./ChatPlanCard";

export { useChatPlan } from "./ChatPlanCard";
export { COMPOSER_BUTTONS } from "./ChatComposer";

interface ChatPaneProps {
  chat: ChatTab;
  visible: boolean;
  flexGrow?: number;
  variant?: "default" | "focus" | "popup";
  allChats: ChatTab[];
  folders?: FolderItem[];
  contextFilePath?: string;
  onOpenChat: (chat: ChatTab) => void;
  onOpenFile?: (path: string, name: string, options?: { line?: number }) => void;
  onOpenPlan?: (chatId: string, title?: string) => void;
  isAgentRunning: boolean;
  onEngage?: () => void;
}

export function ChatPane({
  chat,
  visible,
  flexGrow,
  variant = "default",
  allChats,
  folders = [],
  contextFilePath,
  onOpenChat,
  onOpenFile,
  onOpenPlan,
  isAgentRunning,
  onEngage,
}: ChatPaneProps) {
  const isFocus = variant === "focus";
  const isPopup = variant === "popup";
  const { openFileFocus } = useFocusWindow();
  const { prefs: translationPrefs } = usePluginUiPrefs("translation");
  const translateMessages =
    isAutoTranslateChat(chat.id, translationPrefs) &&
    !isEnglishLang(readTranslationUiLang());
  useEffect(() => {
    if (!translateMessages || !visible) return;
    const t = window.setTimeout(() => requestChatTranslateWalk(), 30);
    return () => window.clearTimeout(t);
  }, [translateMessages, visible, chat.id]);
  useEffect(() => {
    if (!visible) return;
    setFocusedChatForAsk(chat.id);
    return () => {
      // Only clear if we still own focus (another pane may have taken over).
      // getFocusedChatForAsk is checked after unmount of prior pane.
    };
  }, [visible, chat.id]);

  useEffect(() => {
    if (!visible || chat.isGroup) return;
    let cancelled = false;
    void (async () => {
      await whenWalkthroughHydrated();
      // Never behind the first-run setup: its Finish runs the chat walkthrough itself.
      await whenFirstRunSetupDone();
      if (cancelled) return;
      await new Promise((r) => window.setTimeout(r, 400));
      if (cancelled) return;
      if (getWalkthroughState().active) return;
      if (isCompleted("chat.composer")) return;
      await startTour("chat.composer");
    })();
    return () => {
      cancelled = true;
    };
  }, [visible, chat.isGroup, chat.id]);
  const [askSession, setAskSession] = useState<AskUserSession | null>(() =>
    getAskUserSessionForConv(chat.id),
  );
  useEffect(() => {
    setAskSession(getAskUserSessionForConv(chat.id));
    return subscribeAskUser(() => setAskSession(getAskUserSessionForConv(chat.id)));
  }, [chat.id]);
  // A terminal command card waiting in this chat keeps the conversation (and the card) shown.
  const hasCommandCards = useHasTerminalApprovals(chat.id);
  const cachedComposer = getCachedChatComposer(chat.id);
  const initialCodingAgent = chat.codingAgent || "ducky";
  const initialComposer =
    cachedComposer && (!cachedComposer.codingAgent || cachedComposer.codingAgent === initialCodingAgent)
      ? cachedComposer
      : undefined;
  const [inputText, setInputText] = useState(initialComposer?.inputText ?? "");
  const [agentMode, setAgentMode] = useState<AgentMode>(initialComposer?.agentMode ?? "agent");
  const [selectedModel, setSelectedModel] = useState(
    initialComposer?.selectedModel ?? chat.model ?? "",
  );
  const [codingAgent, setCodingAgent] = useState(initialCodingAgent);
  const [thinkingEffort, setThinkingEffort] = useState(chat.thinkingEffort || "off");
  const [catalogReady, setCatalogReady] = useState(() => isModelsCatalogReady());
  const [modelsCount, setModelsCount] = useState(() => getCachedModels()?.length ?? 0);
  const pluginContrib = usePluginContributions();
  const hasApiKey = useHasApiKey();
  const [contextPanelOpen, setContextPanelOpen] = useState(false);
  const [changesOpen, setChangesOpen] = useLedgerOpen();
  const [sessionFiles, setSessionFiles] = useState<SessionFile[]>([]);
  const [contextUsage, setContextUsage] = useState<ContextUsage>({
    used_tokens: 0,
    context_limit: 128000,
    input_tokens: 0,
    output_tokens: 0,
  });
  const [codingAgents, setCodingAgents] = useState(() => getCachedCodingAgents());
  const [modelSupportsVision, setModelSupportsVision] = useState(false);
  const [selectedModelDisplayName, setSelectedModelDisplayName] = useState(
    initialComposer?.selectedModelDisplayName ?? chat.model ?? "",
  );
  const [isDragOver, setIsDragOver] = useState(false);
  const [caret, setCaret] = useState(0);
  const [modelPickerSignal, setModelPickerSignal] = useState(0);
  const openModelPicker = useCallback(() => setModelPickerSignal((n) => n + 1), []);
  // A chat with no model: why (noModelText), how many messages Ducky keeps for it until a
  // model is picked (they run then), and whether the composer's text sends on that pick.
  const [noModelText, setNoModelText] = useState("");
  const [heldForModel, setHeldForModel] = useState(0);
  // The chat's model is on a gateway that isn't connected (the last send said so).
  const [notConnected, setNotConnected] = useState<{ provider: string; label: string } | null>(null);
  const sendAfterPickRef = useRef(false);
  const pickerContextRef = useRef({ visible, selectedModel: "" });
  const { chatPlan, chatPlanProgress, setChatPlan, setChatPlanProgress } = useChatPlan(chat.id);
  const planAllDone = usePlanAllDone(chatPlan, chatPlanProgress);
  const listRef = useRef<VirtualChatMessageListHandle>(null);
  const [paneHost, setPaneHost] = useState<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLDivElement>(null);
  const loadedChatIdRef = useRef(chat.id);
  const paneScopeClass = useScopedClass("chat-pane");
  const { shellRef, setZoomScale } = useChatColumnWidth();
  const handleChatZoomChange = useCallback((zoom: number) => {
    // Live scale with zoom — zoom-out restores the settings base width (e.g. 960).
    setZoomScale(zoom);
  }, [setZoomScale]);

  const {
    attachments,
    error: attachmentError,
    hasImages,
    hasPendingVideos,
    readInParts,
    retryVideo,
    addFiles,
    removeAttachment,
    updateAttachmentImage,
    clearAttachments,
    restoreAttachments,
    replaceAttachments,
    toApiAttachments,
  } = useComposerAttachments(initialComposer?.attachments ?? [], {
    convId: chat.id, model: selectedModel, modelLabel: selectedModelDisplayName, codingAgent,
  });

  const {
    messages,
    hydrated,
    loadError,
    streamBuffer,
    streamThinking,
    streamStatus,
    agentRunning,
    activeRunId,
    hasNewBelow,
    isAtBottom,
    reloadMessages,
    loadOlder,
    appendUserMessage,
    continueInterrupted,
    setActiveRunId,
    onAtBottomChange,
    stopOptimistic,
    stopRun,
    rewindAndAppendUser,
  } = useChatMessages(chat.id, visible, isAgentRunning);

  const turnTimer = useChatTurnTimerState(chat.id);
  const linkedAgents = useLinkedAgents(chat.id, messages, allChats);
  // Group roundtables speak in-place — never park the feed on "waiting for linked".
  const waitingLinked = chat.isGroup
    ? []
    : linkedAgents.filter((a) => a.status === "running");
  const isWaitingOnLinked = !chat.isGroup && agentRunning && waitingLinked.length > 0;
  // A leader whose turn ended while its team still works: name who it waits on.
  const runningIds = useRunningAgents();
  const waitingOnAgents = useMemo(
    () => (agentRunning ? [] : runningTeamChats(chat.id, folders, runningIds)),
    [agentRunning, chat.id, folders, runningIds],
  );
  // Above the composer: this chat's own running turn (its plan's progress), then each agent
  // it waits on (that agent's sub plan in this chat's plan).
  const workRows = useMemo<WorkRow[]>(() => {
    const linked = chat.isGroup ? [] : linkedAgents.filter((a) => a.status === "running");
    const waited = agentRunning ? linked.map((a) => ({ convId: a.childConvId, name: a.title })) : waitingOnAgents;
    const own = chatPlanProgress?.total
      ? { done: chatPlanProgress.completed + chatPlanProgress.cancelled, total: chatPlanProgress.total }
      : null;
    return [
      ...(agentRunning ? [{ convId: chat.id, name: chat.name, progress: own }] : []),
      ...waited.map((a) => ({ ...a, progress: subPlanProgress(chatPlan?.nodes, a.convId) })),
    ];
  }, [agentRunning, chat.id, chat.name, chat.isGroup, linkedAgents, waitingOnAgents, chatPlan, chatPlanProgress]);
  // One cached roster per group (shared with the team bar and sidebar); the chat's own
  // copy shows until the store answers.
  const roster = useRoster(chat.isGroup ? chat.id : "");
  const groupMembers = useMemo<GroupMemberDto[]>(
    () => (chat.isGroup ? roster?.members ?? chat.groupMembers ?? [] : []),
    [chat.isGroup, roster, chat.groupMembers],
  );
  const leaderConvId = chat.isGroup ? roster?.leaderConvId ?? chat.leaderConvId ?? "" : "";
  const handleMembersChange = useCallback((members: GroupMemberDto[]) => setRoster(chat.id, members), [chat.id]);
  const [liveVoice, setLiveVoice] = useState(false);
  const [liveVoiceHandlers, setLiveVoiceHandlers] = useState<LiveVoiceUiHandlers | null>(null);
  const [promptQueue, setPromptQueueState] = useState<QueuedPrompt[]>(() => getPromptQueue(chat.id));
  useEffect(() => {
    setPromptQueueState(getPromptQueue(chat.id));
    return subscribePromptQueue(chat.id, () => setPromptQueueState(getPromptQueue(chat.id)));
  }, [chat.id]);
  const handleLiveVoiceChange = useCallback((live: boolean, handlers: LiveVoiceUiHandlers | null) => {
    setLiveVoice(live);
    setLiveVoiceHandlers(handlers);
  }, []);
  const { rows, editableRowId, hasInlineTurnContent, activityLines } = useChatRowModel({
    messages,
    agentRunning,
    activeRunId,
    streamBuffer,
    streamThinking,
  });
  // Keep the footer clock after the turn ends so we can see how long it took.
  const showIdleTurnTimer = !agentRunning && turnTimer.hasTimer && !turnTimer.running;
  const showFooterActivity = agentRunning || showIdleTurnTimer;

  useEffect(() => {
    // On a pane switching chats, do not write the previous chat's composer
    // state into the new chat before its own state has been restored.
    if (loadedChatIdRef.current !== chat.id) return;
    setCachedChatComposer(chat.id, {
      inputText,
      agentMode,
      selectedModel,
      selectedModelDisplayName,
      codingAgent,
      attachments: toApiAttachments(),
    });
  }, [chat.id, inputText, agentMode, selectedModel, selectedModelDisplayName, codingAgent, attachments, toApiAttachments]);

  // Undo history of the composer (text, caret, attachments). Same guard as the cache
  // above, and declared before the chat-switch restore below for the same reason.
  useEffect(() => {
    if (loadedChatIdRef.current !== chat.id) return;
    recordComposer(chat.id, inputText, caret, toApiAttachments());
  }, [chat.id, inputText, caret, attachments, toApiAttachments]);

  /** Ctrl+Z / Ctrl+Y inside the composer. True when the key was a history key. */
  const handleComposerHistoryKey = (event: KeyboardEvent<HTMLDivElement>): boolean => {
    const action = composerHistoryKey(event);
    if (!action) return false;
    // Always ours in the composer: the browser's own undo cannot follow coded edits.
    event.preventDefault();
    const snapshot = action === "undo" ? undoComposer(chat.id) : redoComposer(chat.id);
    if (!snapshot) return true;
    setInputText(snapshot.text);
    setCaret(snapshot.caret);
    if (!sameAttachments(snapshot.attachments, toApiAttachments())) replaceAttachments(snapshot.attachments);
    return true;
  };

  useEffect(() => {
    // Server echoes for model/agent changes must not overwrite the local
    // selection with a stale cache entry. Restore only when the pane changes chat.
    if (loadedChatIdRef.current === chat.id) return;
    loadedChatIdRef.current = chat.id;
    setNoModelText("");
    setHeldForModel(0);
    sendAfterPickRef.current = false;
    const cached = getCachedChatComposer(chat.id);
    const nextCodingAgent = chat.codingAgent || "ducky";
    const matchingCached =
      cached && (!cached.codingAgent || cached.codingAgent === nextCodingAgent) ? cached : undefined;
    setInputText(matchingCached?.inputText ?? "");
    setAgentMode(matchingCached?.agentMode ?? "agent");
    setSelectedModel(matchingCached?.selectedModel ?? chat.model ?? "");
    setSelectedModelDisplayName(matchingCached?.selectedModelDisplayName ?? chat.model ?? "");
    setCodingAgent(nextCodingAgent);
    setThinkingEffort(chat.thinkingEffort || "off");
    replaceAttachments(matchingCached?.attachments ?? []);
  }, [chat.id, chat.model, chat.codingAgent, chat.thinkingEffort, replaceAttachments]);

  // New-ducky race: tab opens before list_all_conversations includes the chat, so
  // the first ChatPane paint is a stub with no model. Adopt model/agent once the
  // folder list hydrates — only while the composer is still empty.
  useEffect(() => {
    if (loadedChatIdRef.current !== chat.id) return;
    const next = nextComposerFromChat({
      selectedModel,
      codingAgent,
      thinkingEffort,
      chatModel: chat.model,
      chatCodingAgent: chat.codingAgent,
      chatThinkingEffort: chat.thinkingEffort,
    });
    if (!next) return;
    setSelectedModel(next.selectedModel);
    setSelectedModelDisplayName(next.selectedModel);
    setCodingAgent(next.codingAgent);
    setThinkingEffort(next.thinkingEffort);
  }, [chat.id, chat.model, chat.codingAgent, chat.thinkingEffort, selectedModel, codingAgent, thinkingEffort]);

  useEffect(() => {
    if (!visible) return;
    const applyDraft = () => {
      const draft = takeComposerDraft(chat.id);
      if (!draft) return;
      setInputText((prev) => (prev.trim() ? `${prev}\n\n${draft}` : draft));
      if (!skipAutoFocus()) requestAnimationFrame(() => textareaRef.current?.focus());
    };
    applyDraft();
    return subscribeComposerDraft(chat.id, applyDraft);
  }, [visible, chat.id]);

  useEffect(() => {
    // On a phone, opening a chat must not pop up the keyboard; a tap on the box does that.
    if (!visible || skipAutoFocus()) return;
    requestAnimationFrame(() => textareaRef.current?.focus({ preventScroll: true }));
  }, [visible, chat.id]);

  const handleStopLinked = useCallback((childConvId: string) => {
    const api = getApi();
    if (!api) return;
    void api.cancel_agent(childConvId);
  }, []);

  const inputTextRef = useRef(inputText);
  inputTextRef.current = inputText;

  const refreshContextUsage = useCallback(async () => {
    const api = getApi();
    if (!api) return;
    const settings = await api.get_settings().catch(() => null);
    const fromDefault = (() => {
      const qualified = (settings?.default_model || "").trim();
      if (!qualified) return "";
      const parsed = parseFavoriteSelection(qualified);
      return parsed?.modelId || "";
    })();
    const model = selectedModel || fromDefault || settings?.agent_model || "";
    if (!model) return;
    try {
      const usage = await api.get_context_usage(chat.id, model, agentMode, inputTextRef.current);
      // An empty answer keeps the last reading: undefined here crashed the screen on used_tokens.
      if (usage) setContextUsage(usage);
    } catch (err) {
      console.error("get_context_usage failed", err);
    }
  }, [chat.id, selectedModel, agentMode]);

  const refreshSessionFiles = useCallback(async () => {
    const api = getApi();
    if (!api) return;
    const files = await api.get_session_files(chat.id);
    setSessionFiles(files);
  }, [chat.id]);

  const handleOpenFile = useCallback(
    (path: string, name: string, options?: { line?: number }) => {
      if (onOpenFile) {
        onOpenFile(path, name, options);
        return;
      }
      void openFileFocus(path, name || basename(path));
    },
    [onOpenFile, openFileFocus],
  );

  const handleContextChanged = useCallback(() => {
    void reloadMessages();
    void refreshContextUsage();
    void refreshSessionFiles();
  }, [reloadMessages, refreshContextUsage, refreshSessionFiles]);

  const handleToggleContextPanel = useCallback(() => {
    setContextPanelOpen((open) => {
      const next = !open;
      if (next) {
        void refreshContextUsage();
        void refreshSessionFiles();
      }
      return next;
    });
  }, [refreshContextUsage, refreshSessionFiles]);

  // Debounce context metering — skip streamBuffer (usage events cover streaming)
  // and avoid firing on every composer keystroke. During a run every tool event
  // changes `messages`; usage events already meter the live turn, so wait for
  // the run to end and meter once instead of once per tool call.
  useEffect(() => {
    if (!visible || agentRunning) return;
    const id = window.setTimeout(() => {
      void refreshContextUsage();
    }, 500);
    return () => window.clearTimeout(id);
  }, [visible, agentRunning, refreshContextUsage, messages]);

  useEffect(() => {
    if (!visible || !contextPanelOpen) return;
    const id = window.setTimeout(() => {
      void refreshContextUsage();
    }, 500);
    return () => window.clearTimeout(id);
  }, [visible, contextPanelOpen, inputText, refreshContextUsage]);

  useAgentEventSubscription(
    chat.id,
    useCallback(
      (event) => {
        if (event.conv_id && event.conv_id !== chat.id) return;
        if (event.type === "model_needed") {
          setNoDefaultModel(true);
          setNoModelText(event.text || "");
          setHeldForModel(event.held || 0);
          const { visible: shown, selectedModel: picked } = pickerContextRef.current;
          if (shown && !(picked || "").trim()) setModelPickerSignal((n) => n + 1);
          return;
        }
        if (event.type === "provider_not_connected") {
          const provider = event.provider || "";
          setNotConnected({ provider, label: event.label || provider });
          return;
        }
        if (event.type === "model_adopted") {
          if (!event.model) return;
          setCodingAgent(event.coding_agent || "ducky");
          setSelectedModel(event.model);
          setSelectedModelDisplayName(event.model);
          return;
        }
        if (event.type === "context_changed") {
          void refreshContextUsage();
          void refreshSessionFiles();
          return;
        }
        if (event.type === "usage") {
          setContextUsage((prev) => ({
            ...prev,
            input_tokens: event.input_tokens ?? prev.input_tokens,
            output_tokens: event.output_tokens ?? prev.output_tokens,
            total_tokens: event.total_tokens ?? (event.input_tokens ?? prev.input_tokens) + (event.output_tokens ?? prev.output_tokens),
            total_cache_read: event.total_cache_read ?? prev.total_cache_read,
            total_cache_write: event.total_cache_write ?? prev.total_cache_write,
            cache_hit_rate: event.cache_hit_rate ?? prev.cache_hit_rate,
            call_count: event.call_count ?? event.calls?.length ?? prev.call_count,
            calls: event.calls ?? prev.calls,
          }));
          void refreshContextUsage();
          return;
        }
        if (event.type === "tool_done") {
          void refreshSessionFiles();
          if (contextPanelOpen) void refreshContextUsage();
          const toolName = event.tool?.name || "";
          if (toolName === "ducky_create_plan" || toolName === "ducky_update_plan") {
            applyPlanToolDone(event, chat.id, { setChatPlan, setChatPlanProgress });
          }
        }
      },
      [chat.id, chat.isGroup, handleContextChanged, refreshContextUsage, refreshSessionFiles, contextPanelOpen],
    ),
    [chat.id, chat.isGroup, handleContextChanged, refreshContextUsage, refreshSessionFiles, contextPanelOpen],
  );

  useEffect(() => {
    if (!contextPanelOpen) return;
    void refreshSessionFiles();
  }, [contextPanelOpen, messages, refreshSessionFiles]);

  const handleModelMetaChange = useCallback(
    ({ supportsVision, name }: { supportsVision: boolean; name: string }) => {
      setModelSupportsVision(supportsVision);
      setSelectedModelDisplayName(name);
    },
    [],
  );

  useEffect(() => {
    const sync = () => {
      setCatalogReady(isModelsCatalogReady());
      setModelsCount(getCachedModels()?.length ?? 0);
    };
    sync();
    return subscribeModelsCatalog(sync);
  }, []);

  useEffect(() => subscribeCodingAgents(() => setCodingAgents(getCachedCodingAgents())), []);

  const modelsLoading = !catalogReady;
  const noModelsAvailable =
    codingAgent === "ducky" &&
    catalogReady &&
    !hasUsableModels({
      modelsCount,
      agents: codingAgents,
      contribCount: pluginContrib.llm_coding_agents.length,
    });
  const externalAgent = codingAgent !== "ducky";

  // True when every invited member runs an external coding agent (Claude Code,
  // Codex, …) or delegates to a nested group — none of them need an Anthropic/
  // OpenAI API key, so the group must not be gated on hasApiKey/noModelsAvailable.
  const groupUsesExternalOnly =
    !!chat.isGroup &&
    groupMembers.length > 0 &&
    groupMembers.every((m) => {
      if (m.is_group) return true;
      const agent = (m.coding_agent || "").trim().toLowerCase();
      return !!agent && agent !== "ducky";
    });

  const hasText = !!inputText.trim();
  const hasContent = hasText || attachments.length > 0;
  // Group hubs have no model of their own — the answering member decides vision.
  const visionBlocked = !chat.isGroup && (hasImages || attachments.some((a) => a.kind === "video")) && !modelSupportsVision && !externalAgent;
  const canCompose =
    (chat.isGroup
      ? groupMembers.length > 0 && (groupUsesExternalOnly || (hasApiKey && !noModelsAvailable))
      : externalAgent || (!noModelsAvailable && hasApiKey && !!selectedModel)) &&
    hasContent &&
    !visionBlocked &&
    !hasPendingVideos;
  // UI-facing "no models" state — suppressed for groups whose every member
  // already runs an external coding agent (Claude Code, …), which needs no
  // API key. Distinct from noModelsAvailable, which single-chat canCompose
  // still uses as-is.
  const modelsUnavailable = noModelsAvailable && !(chat.isGroup && groupUsesExternalOnly);
  // No model on this chat: it takes the Default Model (agent and model), as a new chat
  // does. Only when no Default Model is set does send open that setting instead.
  // Not before the chat has loaded: a tab opened before the chat list holds the chat
  // is a stub with no agent or model, and flashed this on Codex and Claude chats.
  const needsModel =
    hydrated && !chat.isGroup && !externalAgent && catalogReady && !(selectedModel || "").trim();
  const [noDefaultModel, setNoDefaultModel] = useState(false);
  const keepDuckyAgent = isDucktactoeChat(chat);
  const adoptDefaultModel = useCallback(async (): Promise<boolean> => {
    const res = await Promise.resolve(getApi()?.adopt_default_model?.(chat.id, keepDuckyAgent)).catch(() => null);
    const model = (res?.ok && res.model) || "";
    setNoDefaultModel(!model);
    if (!model) {
      setNoModelText(res?.error || "");
      setHeldForModel(res?.held || 0);
      if ((res?.held || 0) > 0 && visible) setModelPickerSignal((n) => n + 1);
      return false;
    }
    setCodingAgent(res?.coding_agent || "ducky");
    setSelectedModel(model);
    setSelectedModelDisplayName(model);
    return true;
  }, [chat.id, keepDuckyAgent, visible]);
  useEffect(() => {
    if (visible && needsModel) void adoptDefaultModel();
  }, [visible, needsModel, adoptDefaultModel]);
  const promptForDefaultModel = needsModel && noDefaultModel;
  // No model and none to take: the person picks one in the composer, and what they (or an
  // agent) sent meanwhile runs then. Only with no models at all does send open Settings.
  const pickModelFirst = promptForDefaultModel && !modelsUnavailable;
  const showDefaultModelCta = modelsUnavailable;
  pickerContextRef.current = { visible, selectedModel };
  const canSend = canCompose && !agentRunning;
  const canQueue = canCompose && agentRunning;
  const isEmpty = messages.length === 0 && !streamBuffer && !agentRunning;

  const paneFlex = flexGrow != null ? `${flexGrow} 1 0%` : "1 1 0%";

  useEffect(() => subscribeLatestChatActivity(chat.id, () => {
    requestAnimationFrame(() => {
      listRef.current?.scrollToLatest();
      onAtBottomChange(true);
      listRef.current?.flashActivity?.();
    });
  }), [chat.id, onAtBottomChange]);

  const dispatchSend = useCallback(
    (
      text: string,
      apiAttachments: MessageAttachmentDto[],
      mode: AgentMode,
      model: string,
      agent: string | undefined,
    ) => {
      const api = getApi();
      if (!api || (!text.trim() && apiAttachments.length === 0)) return;
      noteUserTurn(chat.id);
      appendUserMessage(text, apiAttachments);
      listRef.current?.scrollToLatest();
      onAtBottomChange(true);
      // After Stop, the UI is idle before the backend thread dies — wait so the
      // follow-up is not rejected as "already running" and the drain lock sticks.
      void (async () => {
        try {
          await api.wait_for_agent_idle?.(chat.id, 5.0);
        } catch {
          /* ignore */
        }
        const res = await api.send_message(chat.id, text, mode, model, contextFilePath, apiAttachments, agent);
        if (res?.run_id) {
          setActiveRunId(res.run_id);
        } else {
          releasePromptDrainLock(chat.id);
          stopOptimistic();
        }
      })();
    },
    [
      appendUserMessage,
      onAtBottomChange,
      chat.id,
      contextFilePath,
      setActiveRunId,
      stopOptimistic,
    ],
  );

  const { availableCommands, runSlashCommand, slashStatus, setSlashStatus } = useSlashCommands({
    chatId: chat.id,
    codingAgent,
    isGroup: Boolean(chat.isGroup),
    setAgentMode,
    openModelPicker,
    addFiles,
    setInputText,
  });
  // keepDraft: a message sent from elsewhere (a swarm card button) leaves the composer as it is.
  const handleSend = (overrideText?: string, opts?: { keepDraft?: boolean }) =>
    composerSend(
      {
        chatId: chat.id,
        inputText,
        setInputText,
        availableCommands,
        runSlashCommand,
        setSlashStatus,
        showDefaultModelCta,
        hasLlmProviders: pluginContrib.llm_providers.length > 0,
        pickModelFirst,
        hasContent,
        sendAfterPickRef,
        adoptDefaultModel,
        openModelPicker,
        toApiAttachments,
        clearAttachments,
        agentRunning,
        canQueue,
        canCompose,
        canSend,
        agentMode,
        selectedModel,
        codingAgent,
        dispatchSend,
      },
      overrideText,
      opts,
    );

  // Swarm card buttons (Build / Change / detail) in this pane send here.
  useSwarmCardSend(paneHost, (text) => handleSend(text, { keepDraft: true }));

  // Another model, or a new turn running, answers "isn't connected".
  useEffect(() => setNotConnected(null), [selectedModel]);
  useEffect(() => {
    if (agentRunning) setNotConnected(null);
  }, [agentRunning]);

  // A model picked (or taken) for a chat that had none: run the messages Ducky kept for it,
  // and send the composer's text that was waiting on the pick.
  useEffect(() => {
    const model = (selectedModel || "").trim();
    if (!model) return;
    if (heldForModel > 0) {
      setHeldForModel(0);
      void getApi()?.run_held_messages?.(chat.id, model, codingAgent);
    }
    if (sendAfterPickRef.current) {
      sendAfterPickRef.current = false;
      handleSend();
    }
  }, [selectedModel, heldForModel]);

  // Drain queue one turn at a time when the agent goes idle.
  useEffect(() => {
    if (agentRunning) {
      releasePromptDrainLock(chat.id);
      return;
    }
    const next = takeNextPromptForDrain(chat.id);
    if (!next) return;
    dispatchSend(next.text, next.attachments, next.mode, next.model, next.codingAgent);
  }, [agentRunning, chat.id, promptQueue.length, dispatchSend]);

  const { handleOpenPlanTab, handleStopTrackingPlan } = useChatPlanActions({
    chatId: chat.id,
    chatName: chat.name,
    chatPlan,
    onOpenPlan,
    setChatPlan,
    setChatPlanProgress,
  });

  const handleStop = useCallback(() => {
    const api = getApi();
    if (!api) return;
    stopRun();
    void api.cancel_agent(chat.id);
  }, [stopRun, chat.id]);

  const handleContinue = useCallback(() => {
    const api = getApi();
    if (!api || chat.isGroup) return;
    continueInterrupted();
    listRef.current?.scrollToLatest();
    onAtBottomChange(true);
    void (async () => {
      try {
        await api.wait_for_agent_idle?.(chat.id, 5.0);
      } catch {
        /* ignore */
      }
      const res = await api.continue_interrupted?.(chat.id, agentMode, selectedModel, contextFilePath, codingAgent);
      if (res?.run_id) {
        setActiveRunId(res.run_id);
      } else {
        stopOptimistic();
      }
    })();
  }, [
    chat.id,
    chat.isGroup,
    continueInterrupted,
    onAtBottomChange,
    agentMode,
    selectedModel,
    codingAgent,
    contextFilePath,
    setActiveRunId,
    stopOptimistic,
  ]);

  const handleResend = useCallback(
    (text: string, mode: AgentMode, model: string, attachments?: MessageAttachmentDto[]) => {
      const api = getApi();
      if (!api) return;
      const trimmed = text.trim();
      const atts = attachments ?? [];
      if (!trimmed && atts.length === 0) return;
      // Cursor: edit last question mid-answer → stop, rewind, rerun with new text.
      void (async () => {
        if (agentRunning) {
          handleStop();
          try {
            await api.wait_for_agent_idle?.(chat.id, 5.0);
          } catch {
            /* ignore */
          }
        }
        rewindAndAppendUser(trimmed, atts.length ? atts : undefined);
        listRef.current?.scrollToLatest();
        onAtBottomChange(true);
        const res = await api.resend_last_user_message(
          chat.id,
          trimmed,
          mode,
          model,
          contextFilePath,
          atts,
          codingAgent,
        );
        if (res?.run_id) {
          setActiveRunId(res.run_id);
        } else {
          stopOptimistic();
        }
      })();
    },
    [
      agentRunning,
      handleStop,
      rewindAndAppendUser,
      onAtBottomChange,
      chat.id,
      codingAgent,
      contextFilePath,
      setActiveRunId,
      stopOptimistic,
    ],
  );

  const { confirm, alert } = useConfirmModal();
  /** Restart from here: the message back in the composer to edit, it and every later
   *  message erased (for the agent too), as if it was never sent. */
  const handleRestartFrom = useCallback(
    (rowId: number, text: string, atts?: MessageAttachmentDto[]) => {
      const api = getApi();
      if (!api?.rewind_to_message) return;
      void (async () => {
        const ok = await confirm({
          title: "Restart from here",
          message:
            "This message goes back in the box for you to edit. It and everything after it are erased from the chat, as if never sent.",
          confirmLabel: "Restart from here",
          danger: true,
        });
        if (ok !== true || !api.rewind_to_message) return;
        if (agentRunning) handleStop();
        const res = await api.rewind_to_message(chat.id, rowId, text);
        if (!res?.ok) {
          await alert(res?.error || "Could not restart from this message.");
          return;
        }
        setInputText(text);
        setCaret(text.length);
        replaceAttachments(atts ?? []);
        await reloadMessages();
        textareaRef.current?.focus();
      })();
    },
    [confirm, alert, agentRunning, handleStop, chat.id, replaceAttachments, reloadMessages],
  );

  const handleJumpToLatest = useCallback(() => {
    listRef.current?.scrollToLatest();
    onAtBottomChange(true);
  }, [onAtBottomChange]);

  const displayModelLabel = noModelsAvailable
    ? "No models"
    : selectedModelDisplayName || selectedModel || "model";


  // Once per drag: dragover fires dozens of times a second, and a bridge call on each
  // one queued up behind the app's other work until the whole program lagged.
  const chatDropArmedRef = useRef(false);
  const armChatAttachDrop = useCallback(() => {
    if (chatDropArmedRef.current) return;
    chatDropArmedRef.current = true;
    getApi()?.set_import_drop_target?.(CHAT_ATTACH_TARGET)?.catch?.(() => {});
  }, []);

  const clearChatAttachDrop = useCallback(() => {
    chatDropArmedRef.current = false;
    getApi()?.set_import_drop_target?.("")?.catch?.(() => {});
  }, []);

  const handleAttachDragOver = useCallback(
    (e: React.DragEvent) => {
      if (!dragHasOsFiles(e.dataTransfer)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      setIsDragOver(true);
      armChatAttachDrop();
    },
    [armChatAttachDrop],
  );

  const handleAttachDragLeave = useCallback(
    (e: React.DragEvent) => {
      if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) {
        setIsDragOver(false);
        clearChatAttachDrop();
      }
    },
    [clearChatAttachDrop],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      if (!dragHasOsFiles(e.dataTransfer)) return;
      e.preventDefault();
      setIsDragOver(false);
      chatDropArmedRef.current = false;
      // Leave CHAT_ATTACH_TARGET for the native document handler to consume as a no-op.
      if (e.dataTransfer.files?.length) {
        void addFiles(e.dataTransfer.files);
      }
    },
    [addFiles],
  );

  const handleEngage = useCallback(() => {
    if (visible) onEngage?.();
  }, [visible, onEngage]);

  return (
    <>
      {visible ? (
        <ScopedCss selector={`.${paneScopeClass}`} rules={{ "--chat-pane-flex": paneFlex }} />
      ) : null}
      <div
        ref={setPaneHost}
        className={`chat-pane-root ${paneScopeClass}${visible ? "" : " chat-pane-root--hidden"}${isDragOver ? " chat-pane-root--drag-over" : ""}${isPopup ? " chat-pane-root--popup" : ""}`}
        {...{ [CHAT_ATTACH_DROP_ATTR]: "" }}
        onPointerDownCapture={handleEngage}
        onKeyDownCapture={handleEngage}
        onDragOver={handleAttachDragOver}
        onDragLeave={handleAttachDragLeave}
        onDrop={handleDrop}
      >
      {/* Live voice stays in the chat box (orb + buttons above the composer): the chat
          remains visible behind it, never a full-screen view. */}
      <div ref={shellRef} className="chat-column-shell">
        <div className="chat-column-gutter chat-column-gutter--left">
        </div>
        <div className="chat-column">
          {chat.isGroup ? (
            <GroupMemberStrip
              groupId={chat.id}
              members={groupMembers}
              leaderConvId={leaderConvId}
              folders={folders}
              allChats={allChats}
              onMembersChange={handleMembersChange}
              onOpenMember={onOpenChat}
            />
          ) : null}
          <ChatRows
            chatId={chat.id}
            isFocus={isFocus}
            isPopup={isPopup}
            hydrated={hydrated}
            loadError={loadError}
            messageCount={messages.length}
            onRetry={() => void reloadMessages()}
            isEmpty={isEmpty}
            showEmptyState={isEmpty && !(askSession && visible) && !hasCommandCards}
            emptyState={{
              hasApiKey: externalAgent || hasApiKey,
              selectedModel,
              modelManagedByAgent: externalAgent,
              modelsLoading: modelsLoading && !externalAgent,
              noModelsAvailable,
              agentMode,
              duckyStyle: chat.duckyStyle,
              isGroup: Boolean(chat.isGroup),
              allowWindowDrag: isFocus,
            }}
            onZoomChange={handleChatZoomChange}
            listRef={listRef}
            list={{
              rows,
              showActivityPanel: showFooterActivity,
              activityHeaderOnly: hasInlineTurnContent,
              activityStatusText: agentRunning ? streamStatus : "",
              showIdleTurnTimer,
              isWaitingOnLinked,
              waitingLinked,
              isAtBottom,
              hasNewBelow,
              onReachTop: loadOlder,
              editableRowId,
              composerMode: agentMode,
              composerModel: selectedModel,
              setComposerModel: setSelectedModel,
              composerCodingAgent: codingAgent,
              setComposerCodingAgent: setCodingAgent,
              convId: chat.id,
              captureAskKeys: visible,
              askSession: askSession && visible ? askSession : null,
              onResend: handleResend,
              onRestartFrom: chat.isGroup ? undefined : handleRestartFrom,
              onStop: agentRunning ? handleStop : undefined,
              onContinue: !agentRunning && !chat.isGroup ? handleContinue : undefined,
              onAtBottomChange,
              onJumpToLatest: handleJumpToLatest,
              onOpenChat,
              onStopLinked: handleStopLinked,
              onOpenFile: handleOpenFile,
              allChats,
              linkedAgents,
              duckyStyle: chat.duckyStyle,
              activityLines,
              chatPlan,
              translateMessages,
              chatPlanProgress,
              planAllDone,
              onOpenPlan: handleOpenPlanTab,
              onStopTrackingPlan: handleStopTrackingPlan,
            }}
          />
          <div className="chat-pane-input-area">
        <PromptQueueBar
          items={promptQueue}
          onEdit={(id) => {
            const item = takePromptForEdit(chat.id, id);
            if (!item) return;
            // Back in the composer: the running turn must not take the old text any more.
            if (agentRunning) void getApi()?.steer_message?.(chat.id, id, "");
            setInputText((draft) =>
              item.text && draft.trim() ? `${item.text}\n\n${draft}` : item.text || draft,
            );
            restoreAttachments(item.attachments);
            setAgentMode(item.mode);
            setSelectedModel(item.model);
            setSelectedModelDisplayName(item.model);
            requestAnimationFrame(() => textareaRef.current?.focus());
          }}
          onSendNow={(id) => {
            // Promote this prompt. A running turn takes it in at its next tool call
            // (user_steered drops it from the queue) and keeps going — never Stop and
            // re-run. If the turn ends first, the idle drain sends it as the next turn.
            setPromptQueue(chat.id, movePromptToFront(promptQueue, id));
            if (agentRunning) {
              const item = promptQueue.find((p) => p.id === id);
              if (item?.attachments.length) {
                // Pictures/files need a turn of their own. Waiting for this one to end left
                // "Send now" doing nothing (Oct 25): stop it, and the idle drain sends this
                // prompt first.
                handleStop();
              } else if (item?.text) {
                void getApi()?.steer_message?.(chat.id, item.id, item.text);
              }
              return;
            }
            // Idle: force-drain this prompt (clear a stuck lock if needed).
            releasePromptDrainLock(chat.id);
            const next = takeNextPromptForDrain(chat.id);
            if (next) dispatchSend(next.text, next.attachments, next.mode, next.model, next.codingAgent);
          }}
          onDelete={(id) => {
            setPromptQueue(chat.id, removePrompt(promptQueue, id));
            if (agentRunning) void getApi()?.steer_message?.(chat.id, id, "");
          }}
        />
        <ChatWorkflowRunCard chatId={chat.id} />
        <ChatPlanCard
          plan={chatPlan}
          progress={chatPlanProgress}
          allDone={planAllDone}
          onOpenPlan={handleOpenPlanTab}
          onStopTracking={handleStopTrackingPlan}
          onOpenFile={handleOpenFile}
        />
        <ChatComposer
          chat={chat}
          allChats={allChats}
          visible={visible}
          isPopup={isPopup}
          paneHost={paneHost}
          textareaRef={textareaRef}
          inputText={inputText}
          setInputText={setInputText}
          caret={caret}
          setCaret={setCaret}
          onHistoryKey={handleComposerHistoryKey}
          isDragOver={isDragOver}
          changesOpen={changesOpen}
          onToggleChanges={() => setChangesOpen((v) => !v)}
          onCloseChanges={() => setChangesOpen(false)}
          liveVoice={liveVoice}
          liveVoiceHandlers={liveVoiceHandlers}
          onLiveVoiceChange={handleLiveVoiceChange}
          agentMode={agentMode}
          setAgentMode={setAgentMode}
          selectedModel={selectedModel}
          setSelectedModel={setSelectedModel}
          codingAgent={codingAgent}
          setCodingAgent={setCodingAgent}
          thinkingEffort={thinkingEffort}
          setThinkingEffort={setThinkingEffort}
          modelPickerSignal={modelPickerSignal}
          onPickModel={openModelPicker}
          onModelMetaChange={handleModelMetaChange}
          showDefaultModelCta={showDefaultModelCta}
          pickModelFirst={pickModelFirst}
          notConnected={notConnected}
          llmProviders={pluginContrib.llm_providers}
          heldForModel={heldForModel}
          noModelText={noModelText}
          visionBlocked={visionBlocked}
          attachmentError={attachmentError}
          displayModelLabel={displayModelLabel}
          slashStatus={slashStatus}
          availableCommands={availableCommands}
          runSlashCommand={runSlashCommand}
          workRows={workRows}
          attachments={attachments}
          readInParts={readInParts}
          removeAttachment={removeAttachment}
          retryVideo={retryVideo}
          updateAttachmentImage={updateAttachmentImage}
          addFiles={addFiles}
          clearAttachments={clearAttachments}
          contextUsage={contextUsage}
          sessionFiles={sessionFiles}
          contextPanelOpen={contextPanelOpen}
          onToggleContextPanel={handleToggleContextPanel}
          onCloseContextPanel={() => setContextPanelOpen(false)}
          onContextChanged={handleContextChanged}
          onOpenFile={handleOpenFile}
          onOpenChat={onOpenChat}
          groupMemberCount={groupMembers.length}
          agentRunning={agentRunning}
          noModelsAvailable={noModelsAvailable}
          modelsUnavailable={modelsUnavailable}
          streamBuffer={streamBuffer}
          hasContent={hasContent}
          canQueue={canQueue}
          canSend={canSend}
          onSend={(text) => handleSend(text)}
          onStop={handleStop}
        />
          </div>
        </div>
        <div className="chat-column-gutter chat-column-gutter--right">
        </div>
      </div>
    </div>
    </>
  );
}
