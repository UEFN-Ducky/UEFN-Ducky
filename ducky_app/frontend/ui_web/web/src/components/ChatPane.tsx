import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icons } from "../icons/Icons";
import { ScopedCss, useScopedClass } from "../utils/scopedCss";
import { ModeSelector } from "./ModeSelector";
import { ModelSelector } from "./ModelSelector";
import { EffortSelector } from "./EffortSelector";
import { modelShowsThinkingEffort } from "./ducky/duckyProfileForm";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { ComposerAttachmentChips } from "./ComposerAttachmentChips";
import { AttachmentPreviewModal } from "./AttachmentPreviewModal";
import { ContextMeter } from "./ContextMeter";
import { ChatPaneEmptyState } from "./ChatPaneEmptyState";
import { VirtualChatMessageList, type VirtualChatMessageListHandle } from "./VirtualChatMessageList";
import { useComposerAttachments } from "../hooks/useComposerAttachments";
import type {
  AgentMode,
  ChatPlan,
  ChatTab,
  ContextUsage,
  FolderItem,
  MessageAttachmentDto,
  PlanProgress,
  SessionFile,
} from "../types/panel";
import { getApi } from "../hooks/usePanelApi";
import { useFocusWindow } from "../hooks/useFocusWindow";
import { useLinkedAgents } from "../hooks/useLinkedAgents";
import { useChatMessages } from "../hooks/useChatMessages";
import { useChatTurnTimer } from "../hooks/useChatTurnTimer";
import { useAgentEventSubscription } from "../hooks/useAgentEventBus";
import { useHasApiKey } from "../hooks/useHasApiKey";
import { buildActivityLines, splitTurnMessages } from "../utils/agentActivity";
import { getCachedChatComposer, setCachedChatComposer, subscribeComposerDraft, takeComposerDraft } from "../hooks/chatComposerCache";
import { nextComposerFromChat } from "../hooks/chatComposerHydrate";
import {
  enqueuePrompt,
  getPromptQueue,
  isPromptDrainLocked,
  makeQueuedPrompt,
  movePromptToFront,
  removePrompt,
  setPromptQueue,
  subscribePromptQueue,
  takeNextPromptForDrain,
  releasePromptDrainLock,
  updatePromptText,
  type QueuedPrompt,
} from "../hooks/promptQueue";
import { PromptQueueBar } from "./PromptQueueBar";
import {
  appendStreamRow,
  buildCommittedChatRows,
  coalesceActivityRows,
} from "../utils/chatMessageGroups";
import { basename } from "../verse-editor/utils/isVerseFile";
import { ChatInputResizeHandle } from "./ChatInputResizeHandle";
import { ChatPlanPopup } from "./ChatPlanPopup";
import {
  getAskUserSessionForConv,
  setFocusedChatForAsk,
  subscribeAskUser,
  type AskUserSession,
} from "../ask-user";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { CtrlWheelZoomRoot } from "./CtrlWheelZoomRoot";
import { useChatColumnWidth } from "../hooks/useChatColumnWidth";
import { isModelsCatalogReady, getCachedModels, subscribeModelsCatalog } from "../hooks/modelsCatalogCache";
import { parseFavoriteSelection } from "../hooks/favoriteModelsCatalog";
import { requestOpenSettings } from "../navigation/openSettingsTab";
import { isAutoTranslateChat } from "../navigation/tabTranslatePrefs";
import { usePluginUiPrefs } from "../hooks/usePluginUiPrefs";
import { requestChatTranslateWalk } from "../navigation/openTranslatedChat";
import { readTranslationUiLang } from "../navigation/openVerseTranslatedTab";
import { isEnglishLang } from "../views/settings/translationLanguages";
import { VoiceControls, type LiveVoiceUiHandlers } from "../voice/VoiceControls";
import { VoiceOverlay } from "../voice/VoiceOverlay";
import { SnipButton } from "./SnipButton";
import { GroupMemberStrip } from "./GroupMemberStrip";
import type { GroupMemberDto } from "../types/panel";
import {
  CHAT_ATTACH_DROP_ATTR,
  CHAT_ATTACH_TARGET,
  dragHasOsFiles,
} from "../utils/osFileDrag";

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
  const [askSession, setAskSession] = useState<AskUserSession | null>(() =>
    getAskUserSessionForConv(chat.id),
  );
  useEffect(() => {
    setAskSession(getAskUserSessionForConv(chat.id));
    return subscribeAskUser(() => setAskSession(getAskUserSessionForConv(chat.id)));
  }, [chat.id]);
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
  const pluginContrib = usePluginContributions();
  const showThinkingEffort = useMemo(() => {
    const thinkingProviders = (pluginContrib.llm_providers || [])
      .filter((p) => p.shows_thinking_effort)
      .map((p) => p.id);
    const agents = (pluginContrib.llm_coding_agents || []).map((a) => ({
      id: a.id,
      shows_thinking_effort: !!a.shows_thinking_effort,
    }));
    const qualified =
      codingAgent !== "ducky"
        ? `${codingAgent}:${selectedModel || "default"}`
        : chat.provider
          ? `${chat.provider}:${selectedModel || "default"}`
          : selectedModel;
    return modelShowsThinkingEffort(qualified, agents, thinkingProviders);
  }, [
    codingAgent,
    selectedModel,
    chat.provider,
    pluginContrib.llm_providers,
    pluginContrib.llm_coding_agents,
  ]);
  const hasApiKey = useHasApiKey();
  const { confirm } = useConfirmModal();
  const [isFocused, setIsFocused] = useState(false);
  const [contextPanelOpen, setContextPanelOpen] = useState(false);
  const [sessionFiles, setSessionFiles] = useState<SessionFile[]>([]);
  const [contextUsage, setContextUsage] = useState<ContextUsage>({
    used_tokens: 0,
    context_limit: 128000,
    input_tokens: 0,
    output_tokens: 0,
  });
  const [catalogReady, setCatalogReady] = useState(() => isModelsCatalogReady());
  const [modelsCount, setModelsCount] = useState(() => getCachedModels()?.length ?? 0);
  const [modelSupportsVision, setModelSupportsVision] = useState(false);
  const [selectedModelDisplayName, setSelectedModelDisplayName] = useState(
    initialComposer?.selectedModelDisplayName ?? chat.model ?? "",
  );
  const [isDragOver, setIsDragOver] = useState(false);
  const [chatPlan, setChatPlan] = useState<ChatPlan | null>(null);
  const [chatPlanProgress, setChatPlanProgress] = useState<PlanProgress | null>(null);
  const planAllDone = useMemo(() => {
    if (!chatPlan) return true;
    if (chatPlanProgress) {
      return chatPlanProgress.total > 0 && chatPlanProgress.completed >= chatPlanProgress.total;
    }
    const todos = chatPlan.todos || [];
    return todos.length > 0 && todos.every((t) => t.status === "completed" || t.status === "cancelled");
  }, [chatPlan, chatPlanProgress]);
  const listRef = useRef<VirtualChatMessageListHandle>(null);
  const paneRootRef = useRef<HTMLDivElement>(null);
  const inputBoxRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const loadedChatIdRef = useRef(chat.id);
  const minTextareaHeight = useRef(60);
  const maxTextareaHeightRef = useRef(Infinity);
  const liveTextareaHeightRef = useRef<number | null>(null);
  const inputResizingRef = useRef(false);
  const [textareaHeight, setTextareaHeight] = useState<number | null>(null);
  const TOP_RESIZE_MARGIN = 16;
  const MIN_MESSAGE_AREA_HEIGHT = 100;
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
    addFiles,
    removeAttachment,
    updateAttachmentImage,
    clearAttachments,
    toApiAttachments,
  } = useComposerAttachments();

  const [previewAttachmentId, setPreviewAttachmentId] = useState<string | null>(null);
  const previewAttachment = useMemo((): MessageAttachmentDto | null => {
    const att = attachments.find((a) => a.id === previewAttachmentId);
    if (!att) return null;
    if (att.kind === "image") {
      return {
        kind: "image",
        name: att.name,
        mime: att.mime,
        data_base64: att.dataUrl.replace(/^data:[^;]+;base64,/, ""),
      };
    }
    return { kind: "file", name: att.name, mime: att.mime, text: att.text };
  }, [attachments, previewAttachmentId]);

  const {
    messages,
    streamBuffer,
    streamThinking,
    streamStatus,
    agentRunning,
    hasNewBelow,
    isAtBottom,
    reloadMessages,
    appendUserMessage,
    setActiveRunId,
    onAtBottomChange,
    stopOptimistic,
    stopRun,
    rewindAndAppendUser,
  } = useChatMessages(chat.id, visible, isAgentRunning);

  const turnTimer = useChatTurnTimer(chat.id);
  const linkedAgents = useLinkedAgents(chat.id, messages, allChats);
  // Group roundtables speak in-place — never park the feed on "waiting for linked".
  const waitingLinked = chat.isGroup
    ? []
    : linkedAgents.filter((a) => a.status === "running");
  const isWaitingOnLinked = !chat.isGroup && agentRunning && waitingLinked.length > 0;
  const [groupMembers, setGroupMembers] = useState<GroupMemberDto[]>(
    () => chat.groupMembers || [],
  );
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
  useEffect(() => {
    setGroupMembers(chat.groupMembers || []);
    if (!chat.isGroup) return;
    const api = getApi();
    if (!api?.group_members) return;
    void api.group_members(chat.id).then((res) => {
      if (res?.ok && Array.isArray(res.members)) setGroupMembers(res.members);
    });
  }, [chat.id, chat.isGroup, chat.groupMembers]);

  const { committed, turnMessages } = useMemo(
    () => splitTurnMessages(messages, agentRunning),
    [messages, agentRunning],
  );

  // Regroup history only when messages change — not on every text/thinking delta.
  const committedRows = useMemo(
    () => buildCommittedChatRows(committed, turnMessages, agentRunning),
    [committed, turnMessages, agentRunning],
  );

  const rows = useMemo(
    () =>
      agentRunning
        ? coalesceActivityRows(
            appendStreamRow(committedRows, streamBuffer, true, streamThinking),
          )
        : committedRows,
    [committedRows, streamBuffer, streamThinking, agentRunning],
  );

  // Only the most recent user message is editable/resendable (Cursor: edit mid-answer).
  const editableRowId = useMemo(() => {
    for (let i = rows.length - 1; i >= 0; i--) {
      const r = rows[i];
      if (r.kind === "bubble" && r.role === "user") return r.id;
    }
    return null;
  }, [rows]);

  const hasInlineTurnContent =
    turnMessages.length > 0 || !!streamBuffer.trim() || !!streamThinking.trim();

  const activityLines = useMemo(
    () =>
      buildActivityLines(
        turnMessages,
        agentRunning ? streamBuffer : "",
        agentRunning ? streamThinking : "",
      ),
    [turnMessages, streamBuffer, streamThinking, agentRunning],
  );

  // Keep the footer clock after the turn ends so we can see how long it took.
  const showIdleTurnTimer = !agentRunning && turnTimer.ms != null && !turnTimer.running;
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
    });
  }, [chat.id, inputText, agentMode, selectedModel, selectedModelDisplayName, codingAgent]);

  useEffect(() => {
    // Server echoes for model/agent changes must not overwrite the local
    // selection with a stale cache entry. Restore only when the pane changes chat.
    if (loadedChatIdRef.current === chat.id) return;
    loadedChatIdRef.current = chat.id;
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
  }, [chat.id, chat.model, chat.codingAgent, chat.thinkingEffort]);

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
      requestAnimationFrame(() => textareaRef.current?.focus());
    };
    applyDraft();
    return subscribeComposerDraft(chat.id, applyDraft);
  }, [visible, chat.id]);

  useEffect(() => {
    if (!visible) return;
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
      setContextUsage(usage);
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
  // and avoid firing on every composer keystroke.
  useEffect(() => {
    if (!visible) return;
    const id = window.setTimeout(() => {
      void refreshContextUsage();
    }, 500);
    return () => window.clearTimeout(id);
  }, [visible, refreshContextUsage, messages]);

  useEffect(() => {
    if (!visible || !contextPanelOpen) return;
    const id = window.setTimeout(() => {
      void refreshContextUsage();
    }, 500);
    return () => window.clearTimeout(id);
  }, [visible, contextPanelOpen, inputText, refreshContextUsage]);

  useEffect(() => {
    let cancelled = false;
    const api = getApi();
    if (!api?.get_plan) {
      setChatPlan(null);
      setChatPlanProgress(null);
      return;
    }
    void api
      .get_plan(chat.id)
      .then((res) => {
        if (cancelled) return;
        setChatPlan(res.plan ?? null);
        setChatPlanProgress(res.progress ?? null);
      })
      .catch(() => {
        if (cancelled) return;
        setChatPlan(null);
        setChatPlanProgress(null);
      });
    return () => {
      cancelled = true;
    };
  }, [chat.id]);

  useAgentEventSubscription(
    chat.id,
    useCallback(
      (event) => {
        if (event.conv_id && event.conv_id !== chat.id) return;
        if (event.type === "plan_updated") {
          setChatPlan(event.plan ?? null);
          setChatPlanProgress(event.progress ?? null);
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
            // Prefer the plan payload from the event (member plans in a group).
            if (event.plan) {
              setChatPlan(event.plan);
              setChatPlanProgress(event.progress ?? null);
              return;
            }
            const api = getApi();
            if (api?.get_plan) {
              void api
                .get_plan(chat.id)
                .then((res) => {
                  setChatPlan(res.plan ?? null);
                  setChatPlanProgress(res.progress ?? null);
                })
                .catch(() => {
                  /* keep existing plan state */
                });
            }
          }
        }
      },
      [chat.id, handleContextChanged, refreshContextUsage, refreshSessionFiles, contextPanelOpen],
    ),
    [chat.id, handleContextChanged, refreshContextUsage, refreshSessionFiles, contextPanelOpen],
  );

  useEffect(() => {
    if (!contextPanelOpen) return;
    void refreshSessionFiles();
  }, [contextPanelOpen, messages, refreshSessionFiles]);

  const computeMaxTextareaHeight = useCallback(() => {
    const inputBox = inputBoxRef.current;
    const textarea = textareaRef.current;
    if (!inputBox || !textarea) return Infinity;

    const inputArea = inputBox.parentElement;
    if (!inputArea) return Infinity;

    const column = inputArea.closest(".chat-column");
    if (!column) return Infinity;

    const chromeHeight = inputBox.offsetHeight - textarea.offsetHeight;
    const columnHeight = column.clientHeight;
    const areaStyle = getComputedStyle(inputArea);
    const areaPadY = parseFloat(areaStyle.paddingTop) + parseFloat(areaStyle.paddingBottom);

    let reservedAboveInput = 0;
    for (const child of Array.from(column.children)) {
      if (child === inputArea) break;
      const el = child as HTMLElement;
      if (el.classList.contains("chat-pane-content")) {
        reservedAboveInput += MIN_MESSAGE_AREA_HEIGHT;
      } else {
        reservedAboveInput += el.offsetHeight;
      }
    }

    const max = columnHeight - reservedAboveInput - areaPadY - chromeHeight - TOP_RESIZE_MARGIN;

    return Math.max(minTextareaHeight.current, max);
  }, []);

  const refreshMaxTextareaHeight = useCallback(() => {
    const max = computeMaxTextareaHeight();
    maxTextareaHeightRef.current = max;
    const el = textareaRef.current;
    if (el) {
      el.style.maxHeight = Number.isFinite(max) ? `${max}px` : "";
    }
    return max;
  }, [computeMaxTextareaHeight]);

  const applyTextareaHeight = useCallback((height: number, commit: boolean) => {
    const max = maxTextareaHeightRef.current;
    const clamped = Math.max(
      minTextareaHeight.current,
      Math.min(Number.isFinite(max) ? max : height, height),
    );
    liveTextareaHeightRef.current = clamped;
    const el = textareaRef.current;
    if (el) {
      el.style.height = `${clamped}px`;
      el.style.minHeight = `${minTextareaHeight.current}px`;
      if (Number.isFinite(max)) el.style.maxHeight = `${max}px`;
    }
    if (commit) {
      setTextareaHeight((prev) => (prev === clamped ? prev : clamped));
    }
    return clamped;
  }, []);

  const onInputResizeStart = useCallback(() => {
    inputResizingRef.current = true;
    refreshMaxTextareaHeight();
    if (liveTextareaHeightRef.current == null) {
      liveTextareaHeightRef.current = textareaHeight ?? minTextareaHeight.current;
    }
  }, [refreshMaxTextareaHeight, textareaHeight]);

  const onInputResize = useCallback(
    (deltaY: number) => {
      const current = liveTextareaHeightRef.current ?? minTextareaHeight.current;
      applyTextareaHeight(current + deltaY, false);
    },
    [applyTextareaHeight],
  );

  const onInputResizeEnd = useCallback(() => {
    inputResizingRef.current = false;
    const h = liveTextareaHeightRef.current;
    if (h != null) setTextareaHeight(h);
  }, []);

  const onInputResizeTap = useCallback(() => {
    refreshMaxTextareaHeight();
    const current =
      liveTextareaHeightRef.current ?? textareaHeight ?? minTextareaHeight.current;
    const isCollapsed = current <= minTextareaHeight.current + 2;
    const next = isCollapsed ? maxTextareaHeightRef.current : minTextareaHeight.current;
    applyTextareaHeight(next, true);
  }, [applyTextareaHeight, refreshMaxTextareaHeight, textareaHeight]);

  useEffect(() => {
    if (!visible || isPopup) return;
    const el = textareaRef.current;
    if (!el) return;
    const measured = el.offsetHeight;
    if (measured > 0) {
      minTextareaHeight.current = measured;
      refreshMaxTextareaHeight();
      applyTextareaHeight(liveTextareaHeightRef.current ?? measured, true);
    }
  }, [visible, isPopup, refreshMaxTextareaHeight, applyTextareaHeight]);

  useEffect(() => {
    // Popup overlay is height-capped; ResizeObserver + setState here oscillates
    // (React #185 maximum update depth) when the composer fights the card size.
    if (!visible || isPopup) return;
    const column = inputBoxRef.current?.closest(".chat-column");
    if (!column) return;

    const clampToPane = () => {
      refreshMaxTextareaHeight();
      // Mid-drag height is written imperatively; committing here would re-render every move.
      if (inputResizingRef.current) return;
      const current = liveTextareaHeightRef.current;
      if (current == null) return;
      applyTextareaHeight(current, false); // RO must not commit setState (React #185)
    };

    const ro = new ResizeObserver(clampToPane);
    ro.observe(column);
    return () => ro.disconnect();
  }, [visible, isPopup, refreshMaxTextareaHeight, applyTextareaHeight]);

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

  const modelsLoading = !catalogReady;
  const noModelsAvailable = codingAgent === "ducky" && catalogReady && modelsCount === 0;
  const externalAgent = codingAgent !== "ducky";

  const hasText = !!inputText.trim();
  // Group chats ignore attachments — members only get the text prompt.
  const hasContent = hasText || (!chat.isGroup && attachments.length > 0);
  const visionBlocked = hasImages && !modelSupportsVision && !externalAgent;
  const canCompose =
    (chat.isGroup
      ? groupMembers.length > 0 && hasApiKey && !noModelsAvailable
      : externalAgent || (!noModelsAvailable && hasApiKey && !!selectedModel)) &&
    hasContent &&
    !visionBlocked;
  const canSend = canCompose && !agentRunning;
  const canQueue = canCompose && agentRunning;
  const isEmpty = messages.length === 0 && !streamBuffer && !agentRunning;

  useEffect(() => {
    if (isPopup) return;
    refreshMaxTextareaHeight();
    const current = liveTextareaHeightRef.current;
    if (current == null) return;
    // Imperative only — commit:true here can ResizeObserver↔setState loop (React #185).
    applyTextareaHeight(current, false);
  }, [isPopup, attachments.length, visionBlocked, attachmentError, refreshMaxTextareaHeight, applyTextareaHeight]);

  const paneFlex = flexGrow != null ? `${flexGrow} 1 0%` : "1 1 0%";

  const sendBtnTitle = noModelsAvailable
    ? "Open Settings → LLMs to add an API key and configure models"
    : canQueue
      ? "Queue follow-up (runs when this turn finishes)"
      : canSend
        ? externalAgent
          ? `Send via ${codingAgent}`
          : "Send"
        : "Add API key, model, and message or attachments";

  const sendBtnClass = noModelsAvailable
    ? "chat-pane-send-btn chat-pane-send-btn--settings-cta"
    : hasContent
      ? canSend || canQueue
        ? "chat-pane-send-btn chat-pane-send-btn--has-text-can-send"
        : "chat-pane-send-btn chat-pane-send-btn--has-text-no-send"
      : "chat-pane-send-btn";

  const dispatchSend = useCallback(
    (text: string, apiAttachments: MessageAttachmentDto[], mode: AgentMode, model: string) => {
      const api = getApi();
      if (!api || (!text.trim() && apiAttachments.length === 0)) return;
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
        const res = await api.send_message(chat.id, text, mode, model, contextFilePath, apiAttachments);
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

  const handleSend = (overrideText?: string) => {
    if (!chat.isGroup && noModelsAvailable) {
      requestOpenSettings("LLMs");
      return;
    }
    const text = (overrideText ?? inputText).trim();
    const apiAttachments = overrideText ? [] : toApiAttachments();
    if (!text && apiAttachments.length === 0) return;

    // Busy / draining → queue (Cursor-like FIFO). Do NOT cancel the live run —
    // cancel+drain made follow-ups feel like an instant resend and the queue
    // never sat. Drain when the turn goes idle (or the user hits Stop).
    const draining = isPromptDrainLocked(chat.id) || getPromptQueue(chat.id).length > 0;
    if (agentRunning || draining) {
      if (agentRunning && !overrideText && !canQueue) return;
      if (!agentRunning && !overrideText && !canCompose) return;
      const item = makeQueuedPrompt(text, {
        attachments: apiAttachments,
        mode: agentMode,
        model: selectedModel,
      });
      if (!item) return;
      enqueuePrompt(chat.id, item);
      setInputText("");
      if (!overrideText) clearAttachments();
      return;
    }

    if (!overrideText && !canSend) return;
    setInputText("");
    if (!overrideText) clearAttachments();
    dispatchSend(text, apiAttachments, agentMode, selectedModel);
  };

  // Drain queue one turn at a time when the agent goes idle.
  useEffect(() => {
    if (agentRunning) {
      releasePromptDrainLock(chat.id);
      return;
    }
    const next = takeNextPromptForDrain(chat.id);
    if (!next) return;
    dispatchSend(next.text, next.attachments, next.mode, next.model);
  }, [agentRunning, chat.id, promptQueue.length, dispatchSend]);

  const handleOpenPlanTab = useCallback(() => {
    // In group chats, the visible plan may belong to a member (plan.chat_id).
    const planChatId = (chatPlan?.chat_id || "").trim() || chat.id;
    onOpenPlan?.(planChatId, chatPlan?.title || chat.name || "Plan");
  }, [onOpenPlan, chat.id, chat.name, chatPlan?.chat_id, chatPlan?.title]);

  const handleStopTrackingPlan = useCallback(async () => {
    const title = chatPlan?.title || "Plan";
    const planChatId = (chatPlan?.chat_id || "").trim() || chat.id;
    if (
      !(await confirm({
        message: `Stop tracking “${title}”? Removes the plan from this chat (plan file deleted). The chat stays.`,
        confirmLabel: "Stop tracking",
        danger: true,
      }))
    ) {
      return;
    }
    const api = getApi();
    if (!api?.delete_plan) return;
    const res = await api.delete_plan(planChatId);
    if (!res.ok) return;
    setChatPlan(null);
    setChatPlanProgress(null);
  }, [chat.id, chatPlan?.chat_id, chatPlan?.title, confirm]);

  const handleStop = useCallback(() => {
    const api = getApi();
    if (!api) return;
    stopRun();
    void api.cancel_agent(chat.id);
  }, [stopRun, chat.id]);

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
      contextFilePath,
      setActiveRunId,
      stopOptimistic,
    ],
  );

  const handleJumpToLatest = useCallback(() => {
    listRef.current?.scrollToLatest();
    onAtBottomChange(true);
  }, [onAtBottomChange]);

  const displayModelLabel = noModelsAvailable
    ? "No models"
    : selectedModelDisplayName || selectedModel || "model";

  const attachDropEnabled = true;

  const armChatAttachDrop = useCallback(() => {
    getApi()?.set_import_drop_target?.(CHAT_ATTACH_TARGET)?.catch?.(() => {});
  }, []);

  const clearChatAttachDrop = useCallback(() => {
    getApi()?.set_import_drop_target?.("")?.catch?.(() => {});
  }, []);

  const handleAttachDragOver = useCallback(
    (e: React.DragEvent) => {
      if (!attachDropEnabled || !dragHasOsFiles(e.dataTransfer)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      setIsDragOver(true);
      armChatAttachDrop();
    },
    [attachDropEnabled, armChatAttachDrop],
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
      if (!attachDropEnabled || !dragHasOsFiles(e.dataTransfer)) return;
      e.preventDefault();
      setIsDragOver(false);
      // Leave CHAT_ATTACH_TARGET for the native document handler to consume as a no-op.
      if (e.dataTransfer.files?.length) {
        void addFiles(e.dataTransfer.files);
      }
    },
    [attachDropEnabled, addFiles],
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
        ref={paneRootRef}
        className={`chat-pane-root ${paneScopeClass}${visible ? "" : " chat-pane-root--hidden"}${isDragOver ? " chat-pane-root--drag-over" : ""}${isPopup ? " chat-pane-root--popup" : ""}`}
        {...(attachDropEnabled ? { [CHAT_ATTACH_DROP_ATTR]: "" } : {})}
        onPointerDownCapture={handleEngage}
        onKeyDownCapture={handleEngage}
        onDragOver={attachDropEnabled ? handleAttachDragOver : undefined}
        onDragLeave={attachDropEnabled ? handleAttachDragLeave : undefined}
        onDrop={attachDropEnabled ? handleDrop : undefined}
      >
      <div ref={shellRef} className="chat-column-shell">
        <div className="chat-column-gutter chat-column-gutter--left">
        </div>
        <div className="chat-column">
          {chat.isGroup ? (
            <GroupMemberStrip
              groupId={chat.id}
              members={groupMembers}
              folders={folders}
              allChats={allChats}
              onMembersChange={setGroupMembers}
              onOpenMember={onOpenChat}
            />
          ) : null}
          <div
            className={`selectable-text chat-pane-content${isFocus ? " chat-pane-content--focus" : ""}${isPopup ? " chat-pane-content--popup" : ""}`}
          >
            <CtrlWheelZoomRoot
              className={`chat-pane-inner${isEmpty ? " chat-pane-inner--empty" : ""}`}
              storageKey={`uefn-panel-chat-zoom:${chat.id}`}
              onZoomChange={handleChatZoomChange}
            >
              {isEmpty && !(askSession && visible) ? (
                <ChatPaneEmptyState
                  hasApiKey={externalAgent || hasApiKey}
                  selectedModel={selectedModel}
                  modelManagedByAgent={externalAgent}
                  modelsLoading={modelsLoading && !externalAgent}
                  noModelsAvailable={noModelsAvailable}
                  agentMode={agentMode}
                  duckyStyle={chat.duckyStyle}
                  isGroup={Boolean(chat.isGroup)}
                  allowWindowDrag={isFocus}
                />
              ) : (
                <VirtualChatMessageList
                  ref={listRef}
                  rows={rows}
                  showActivityPanel={showFooterActivity}
                  activityHeaderOnly={hasInlineTurnContent}
                  activityStatusText={agentRunning ? streamStatus : ""}
                  showIdleTurnTimer={showIdleTurnTimer}
                  isWaitingOnLinked={isWaitingOnLinked}
                  waitingLinked={waitingLinked}
                  isAtBottom={isAtBottom}
                  hasNewBelow={hasNewBelow}
                  editableRowId={editableRowId}
                  composerMode={agentMode}
                  composerModel={selectedModel}
                  composerCodingAgent={codingAgent}
                  setComposerCodingAgent={setCodingAgent}
                  convId={chat.id}
                  captureAskKeys={visible}
                  askSession={askSession && visible ? askSession : null}
                  onResend={handleResend}
                  onStop={agentRunning ? handleStop : undefined}
                  onAtBottomChange={onAtBottomChange}
                  onJumpToLatest={handleJumpToLatest}
                  onOpenChat={onOpenChat}
                  onStopLinked={handleStopLinked}
                  onOpenFile={handleOpenFile}
                  allChats={allChats}
                  linkedAgents={linkedAgents}
                  duckyStyle={chat.duckyStyle}
                  activityLines={activityLines}
                  chatPlan={chatPlan}
                  translateMessages={translateMessages}
                  chatPlanProgress={chatPlanProgress}
                  planAllDone={planAllDone}
                  onOpenPlan={handleOpenPlanTab}
                  onStopTrackingPlan={handleStopTrackingPlan}
                />
              )}
            </CtrlWheelZoomRoot>
          </div>
          <div className="chat-pane-input-area">
        <PromptQueueBar
          items={promptQueue}
          onEdit={(id, text) => setPromptQueue(chat.id, updatePromptText(promptQueue, id, text))}
          onSendNow={(id) => {
            // Promote this prompt, then stop the live turn so the idle drain
            // picks it up next — same end state as Stop, without a second click.
            setPromptQueue(chat.id, movePromptToFront(promptQueue, id));
            if (agentRunning) {
              handleStop();
              return;
            }
            // Idle: force-drain this prompt (clear a stuck lock if needed).
            releasePromptDrainLock(chat.id);
            const next = takeNextPromptForDrain(chat.id);
            if (next) dispatchSend(next.text, next.attachments, next.mode, next.model);
          }}
          onDelete={(id) => setPromptQueue(chat.id, removePrompt(promptQueue, id))}
        />
        {chatPlan != null && !planAllDone ? (
          <div className="chat-pane-plan-dock">
            <ChatPlanPopup
              plan={chatPlan}
              progress={chatPlanProgress}
              onOpenPlan={handleOpenPlanTab}
              onStopTracking={handleStopTrackingPlan}
              onOpenFile={handleOpenFile}
            />
          </div>
        ) : null}
        <div
          ref={inputBoxRef}
          className={`no-drag chat-pane-input-box${isFocused ? " chat-pane-input-box--focused" : ""}${isDragOver ? " chat-pane-input-box--drag-over" : ""}${liveVoice ? " chat-pane-input-box--voice" : ""}`}
        >
          <div className={`voice-panel-wrapper${liveVoice ? " is-open" : ""}`}>
            <div className="voice-panel-inner">
              {liveVoice && liveVoiceHandlers ? (
                <VoiceOverlay
                  chatId={chat.id}
                  open
                  inline
                  showPickers
                  onClose={liveVoiceHandlers.onClose}
                  onBack={liveVoiceHandlers.onBack}
                  onForward={liveVoiceHandlers.onForward}
                  onNewest={liveVoiceHandlers.onNewest}
                  hasPrev={liveVoiceHandlers.hasPrev}
                  hasNext={liveVoiceHandlers.hasNext}
                  hasNewer={liveVoiceHandlers.hasNewer}
                  voiceId={liveVoiceHandlers.voiceId}
                  speed={liveVoiceHandlers.speed}
                  setVoiceId={liveVoiceHandlers.setVoiceId}
                  setSpeed={liveVoiceHandlers.setSpeed}
                  processTalk={liveVoiceHandlers.processTalk}
                  setProcessTalk={liveVoiceHandlers.setProcessTalk}
                  muted={liveVoiceHandlers.muted}
                />
              ) : null}
            </div>
          </div>

          <div className="chat-pane-composer-pane chat-pane-composer-pane--type">
            {(noModelsAvailable || visionBlocked || attachmentError) && (
              <div className={`composer-attach-warning${visionBlocked ? " is-vision-blocked" : ""}`}>
                {noModelsAvailable
                  ? "No models available — open Settings → LLMs to add an API key and configure a provider."
                  : visionBlocked
                    ? `${displayModelLabel} cannot use images — switch to a vision model or remove images.`
                    : attachmentError}
              </div>
            )}
            <ComposerAttachmentChips
              attachments={attachments}
              onRemove={removeAttachment}
              onPreview={(att) => setPreviewAttachmentId(att.id)}
            />
            <AttachmentPreviewModal
              open={previewAttachment !== null}
              attachment={previewAttachment}
              onClose={() => setPreviewAttachmentId(null)}
              onSaveDrawing={(dataUrl) => {
                if (previewAttachmentId) updateAttachmentImage(previewAttachmentId, dataUrl);
              }}
            />
            <ChatInputResizeHandle
              onDrag={onInputResize}
              onDragStart={onInputResizeStart}
              onDragEnd={onInputResizeEnd}
              onTap={onInputResizeTap}
            />
            <textarea
              ref={textareaRef}
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setIsFocused(false)}
              onKeyDown={(e) => {
                if (!chat.isGroup && noModelsAvailable) return;
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder={
                liveVoice && liveVoiceHandlers?.muted
                  ? "You're muted — type to chat (Shift+Enter for newline)"
                  : chat.isGroup
                    ? groupMembers.length === 0
                      ? "Invite a ducky above to start the roundtable…"
                      : agentRunning
                        ? "Add a follow-up… (Shift+Enter for newline)"
                        : "Message the group… (Shift+Enter for newline)"
                    : noModelsAvailable
                      ? "No models available — use the button below to open Settings → LLMs"
                      : agentRunning
                        ? "Add a follow-up… (Shift+Enter for newline)"
                        : `Ask ${displayModelLabel}... (Shift+Enter for newline)`
              }
              className="chat-pane-textarea"
              disabled={!chat.isGroup && noModelsAvailable}
              style={
                textareaHeight != null
                  ? {
                      height: textareaHeight,
                      minHeight: minTextareaHeight.current,
                      maxHeight: Number.isFinite(maxTextareaHeightRef.current)
                        ? maxTextareaHeightRef.current
                        : undefined,
                    }
                  : undefined
              }
            />
          </div>

          <div className="chat-pane-input-toolbar">
            <div className="chat-pane-input-toolbar-left">
              {!chat.isGroup ? (
                <ModeSelector activeMode={agentMode} setMode={setAgentMode} />
              ) : null}
              <ContextMeter
                usedTokens={contextUsage.used_tokens}
                contextLimit={contextUsage.context_limit}
                inputTokens={contextUsage.input_tokens}
                outputTokens={contextUsage.output_tokens}
                usage={contextUsage}
                sessionFiles={sessionFiles}
                convId={chat.id}
                omitted={contextUsage.omitted}
                agentMode={agentMode}
                model={selectedModel}
                agentRunning={agentRunning}
                panelOpen={contextPanelOpen}
                onTogglePanel={handleToggleContextPanel}
                onClosePanel={() => setContextPanelOpen(false)}
                onOpenFile={handleOpenFile}
                onContextChanged={handleContextChanged}
                onClearDraft={() => setInputText("")}
              />
              {!chat.isGroup ? (
                <>
                  <div className="chat-pane-toolbar-divider" />
                  <ModelSelector
                    selectedModel={selectedModel}
                    setSelectedModel={setSelectedModel}
                    codingAgent={codingAgent}
                    setCodingAgent={setCodingAgent}
                    convId={chat.id}
                    preserveSelection
                    onModelMetaChange={handleModelMetaChange}
                  />
                  {showThinkingEffort ? (
                    <EffortSelector
                      convId={chat.id}
                      provider={chat.provider || codingAgent || "anthropic"}
                      value={thinkingEffort}
                      onChange={setThinkingEffort}
                    />
                  ) : null}
                </>
              ) : null}
            </div>

            <div className="chat-pane-input-toolbar-right">
              <SnipButton
                disabled={noModelsAvailable}
                onCaptured={(file, meta) =>
                  void addFiles([file], {
                    imagesOnly: true,
                    projectPath: meta?.projectPath,
                  })
                }
              />
              <VoiceControls
                chatId={chat.id}
                disabled={
                  noModelsAvailable ||
                  (Boolean(chat.isGroup) && groupMembers.length === 0)
                }
                inputText={inputText}
                setInputText={setInputText}
                onSend={(text) => handleSend(text)}
                streamText={streamBuffer}
                agentRunning={agentRunning}
                duckyVoice={chat.ttsVoice}
                duckySpeed={chat.ttsSpeed}
                isGroup={Boolean(chat.isGroup)}
                onLiveChange={handleLiveVoiceChange}
              />
              {agentRunning ? (
                <>
                  {hasContent ? (
                    <button
                      type="button"
                      onClick={() => handleSend()}
                      disabled={!canQueue}
                      title={sendBtnTitle}
                      className={sendBtnClass}
                    >
                      <Icons.Send />
                    </button>
                  ) : null}
                  <button type="button" onClick={handleStop} className="chat-pane-stop-btn">
                    <div className="chat-pane-stop-btn-icon" />
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => handleSend()}
                  disabled={!noModelsAvailable && !canSend}
                  title={sendBtnTitle}
                  className={sendBtnClass}
                >
                  {noModelsAvailable ? <Icons.Settings /> : <Icons.Send />}
                </button>
              )}
            </div>
          </div>
        </div>
          </div>
        </div>
        <div className="chat-column-gutter chat-column-gutter--right">
        </div>
      </div>
    </div>
    </>
  );
}
