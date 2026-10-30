import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type Dispatch,
  type KeyboardEvent,
  type MutableRefObject,
  type RefObject,
  type SetStateAction,
} from "react";
import { Icons } from "../icons/Icons";
import { ChatPermissionsButton } from "./ChatPermissionsButton";
import { ContextMenu, useContextMenuState, type ContextMenuItem } from "./ContextMenu";
import { useHeaderVisibility } from "../hooks/useHeaderVisibility";
import { ModelSelector } from "./ModelSelector";
import { ComposerAttachmentChips } from "./ComposerAttachmentChips";
import { AttachmentPreviewModal } from "./AttachmentPreviewModal";
import { ContextMeter } from "./ContextMeter";
import type { useComposerAttachments } from "../hooks/useComposerAttachments";
import type { usePluginContributions } from "../hooks/usePluginContributions";
import type {
  AgentMode,
  ChatTab,
  ContextUsage,
  MessageAttachmentDto,
  SessionFile,
} from "../types/panel";
import { getApi } from "../hooks/usePanelApi";
import {
  enqueuePrompt,
  getPromptQueue,
  isPromptDrainLocked,
  makeQueuedPrompt,
} from "../hooks/promptQueue";
import { ChatInputResizeHandle } from "./ChatInputResizeHandle";
import { ModelSetupNotice } from "./ModelSetupNotice";
import { openLlmsProviderSettings } from "../navigation/openSettingsTab";
import { openGatewaySetup } from "./GatewaySetupNotice";
import { useMergedRef, useUiTarget } from "../ui-targets/registry";
import { VoiceControls, type LiveVoiceUiHandlers } from "../voice/VoiceControls";
import { VoiceOverlay } from "../voice/VoiceOverlay";
import { SnipButton } from "./SnipButton";
import { AttachMenuButton } from "./AttachMenuButton";
import { ChatChangesButton, ChatChangesSlide } from "./ChatChangesDrawer";
import { captureSnipFile } from "./snipCapture";
import { ComposerDraft, composerCaret, composerSetCaret, enterInsertsNewline } from "./ComposerDraft";
import { SlashCommandMenu } from "./SlashCommandMenu";
import {
  filterChatRefs,
  insertChatRef,
  instantInnerRefs,
  mergeInnerRefs,
  readCaretToken,
  type ChatRef,
} from "./chatReferences";
import { useChatReferenceCatalog, useFileRefMatches } from "./useChatReferenceCatalog";
import { answerAskUserWithMessage } from "../ask-user";
import { useInnerRefSearch } from "./useInnerRefSearch";
import {
  commandsForScope,
  composerPlaceholder,
  filterCommands,
  matchSlashCommand,
  readSlashQuery,
  type SlashCommand,
} from "./slashCommands";

type Attachments = ReturnType<typeof useComposerAttachments>;

/** Shown while the @ / slash catalog loads for the first time; picking it does nothing. */
const LOADING_REF: ChatRef = { trigger: "@", id: "__loading__", label: "Loading…", group: "", href: "" };
const REF_TRIGGER = /[@/~]/;
type OpenFile = (path: string, name: string, options?: { line?: number }) => void;

/** The composer buttons its right-click menu can hide (Send and Stop always stay). */
export const COMPOSER_BUTTONS: { id: string; label: string; Icon: () => JSX.Element }[] = [
  { id: "permissions", label: "Mode and permissions", Icon: Icons.Shield },
  { id: "context", label: "Context", Icon: Icons.Brain },
  { id: "model", label: "Model", Icon: Icons.Sparkles },
  { id: "changes", label: "Changes", Icon: Icons.Clock },
  { id: "snip", label: "Snip", Icon: Icons.Crop },
  { id: "attach", label: "Attach", Icon: Icons.Paperclip },
  { id: "voice", label: "Voice", Icon: Icons.Mic },
];

/** The chat's slash commands, how to run one, and the status line they report to. */
export function useSlashCommands({
  chatId,
  codingAgent,
  isGroup,
  setAgentMode,
  openModelPicker,
  addFiles,
  setInputText,
}: {
  chatId: string;
  codingAgent: string;
  isGroup: boolean;
  setAgentMode: Dispatch<SetStateAction<AgentMode>>;
  openModelPicker: () => void;
  addFiles: Attachments["addFiles"];
  setInputText: Dispatch<SetStateAction<string>>;
}) {
  const [slashStatus, setSlashStatus] = useState("");
  const availableCommands = useMemo(
    () => commandsForScope({ codingAgent, isGroup }),
    [codingAgent, isGroup],
  );

  useEffect(() => {
    if (!slashStatus) return;
    const t = window.setTimeout(() => setSlashStatus(""), 5000);
    return () => window.clearTimeout(t);
  }, [slashStatus]);

  const runSlashCommand = useCallback(
    (command: SlashCommand, argument: string) => {
      void command.run({
        chatId,
        argument,
        setAgentMode,
        openModelPicker,
        captureSnip: () => {
          void (async () => {
            const snip = await captureSnipFile(chatId);
            if (!snip) {
              setSlashStatus("Snip cancelled — nothing captured.");
              return;
            }
            await addFiles([snip.file], { imagesOnly: true, projectPath: snip.projectPath });
          })();
        },
        // Re-opening the bare prefix lists everything this chat supports.
        showHelp: () => setInputText("/"),
        report: setSlashStatus,
      });
    },
    [chatId, addFiles],
  );

  return { availableCommands, runSlashCommand, slashStatus, setSlashStatus };
}

/** Per-chat note under the box (e.g. "Sent — the agent takes it at its next step"). */
const composerNotes = new Map<string, string>();
const composerNoteListeners = new Set<() => void>();

function setComposerNote(chatId: string, note: string) {
  if (note) composerNotes.set(chatId, note);
  else composerNotes.delete(chatId);
  for (const listener of composerNoteListeners) listener();
}

function subscribeComposerNotes(listener: () => void): () => void {
  composerNoteListeners.add(listener);
  return () => composerNoteListeners.delete(listener);
}

/**
 * A `send_message` reply of `{queued: true, note}` (the agent was busy and takes the message
 * at its next step): shows the note under that chat's box and returns true, so the caller
 * keeps the message as sent and never reports "Agent already running". Other replies: false.
 */
export function noteQueuedSend(chatId: string, reply: unknown): boolean {
  const res = reply as { queued?: unknown; note?: unknown } | null | undefined;
  if (!res?.queued) return false;
  setComposerNote(chatId, String(res.note || "Sent — the agent takes it at its next step"));
  return true;
}

/** Everything the send path reads; built fresh by the pane each render. */
export interface ComposerSendContext {
  chatId: string;
  inputText: string;
  setInputText: Dispatch<SetStateAction<string>>;
  availableCommands: SlashCommand[];
  runSlashCommand: (command: SlashCommand, argument: string) => void;
  setSlashStatus: (text: string) => void;
  showDefaultModelCta: boolean;
  hasLlmProviders: boolean;
  pickModelFirst: boolean;
  hasContent: boolean;
  sendAfterPickRef: MutableRefObject<boolean>;
  adoptDefaultModel: () => Promise<boolean>;
  openModelPicker: () => void;
  toApiAttachments: () => MessageAttachmentDto[];
  clearAttachments: () => void;
  agentRunning: boolean;
  canQueue: boolean;
  canCompose: boolean;
  canSend: boolean;
  agentMode: AgentMode;
  selectedModel: string;
  codingAgent: string;
  dispatchSend: (
    text: string,
    apiAttachments: MessageAttachmentDto[],
    mode: AgentMode,
    model: string,
    agent: string | undefined,
  ) => void;
}

// keepDraft: a message sent from elsewhere (a swarm card button) leaves the composer as it is.
export function composerSend(
  ctx: ComposerSendContext,
  overrideText?: string,
  { keepDraft = false }: { keepDraft?: boolean } = {},
) {
  const text = (overrideText ?? ctx.inputText).trim();

  // A recognised command acts on the panel — it never reaches the ducky, so
  // it runs before the "no models" gate (that is when /model matters most).
  const slash = overrideText ? null : matchSlashCommand(text, ctx.availableCommands);
  if (slash) {
    if (slash.command.requiresArgument && !slash.argument) {
      ctx.setSlashStatus(`/${slash.command.name} needs ${slash.command.args ?? "an argument"}.`);
      return;
    }
    ctx.setInputText("");
    ctx.runSlashCommand(slash.command, slash.argument);
    return;
  }

  // An open ducky_ask_user in this chat: the message is its answer, never also a steer.
  if (!overrideText && answerAskUserWithMessage(ctx.chatId, text)) {
    ctx.setInputText("");
    return;
  }

  if (ctx.showDefaultModelCta) {
    // No AI connected: a Default Model can't be set yet, so connect one first.
    if (!ctx.hasLlmProviders && openGatewaySetup()) return;
    openLlmsProviderSettings();
    return;
  }
  if (ctx.pickModelFirst) {
    // The text stays and sends the moment a model is picked, or a model set since is taken.
    ctx.sendAfterPickRef.current = ctx.hasContent;
    void ctx.adoptDefaultModel().then((adopted) => {
      if (!adopted) ctx.openModelPicker();
    });
    return;
  }
  const apiAttachments = overrideText ? [] : ctx.toApiAttachments();
  if (!text && apiAttachments.length === 0) return;

  // Busy / draining → queue (Cursor-like FIFO). Do NOT cancel the live run —
  // cancel+drain made follow-ups feel like an instant resend and the queue
  // never sat. Drain when the turn goes idle (or the user hits Stop).
  const draining = isPromptDrainLocked(ctx.chatId) || getPromptQueue(ctx.chatId).length > 0;
  if (ctx.agentRunning || draining) {
    if (ctx.agentRunning && !overrideText && !ctx.canQueue) return;
    if (!ctx.agentRunning && !overrideText && !ctx.canCompose) return;
    const item = makeQueuedPrompt(text, {
      attachments: apiAttachments,
      mode: ctx.agentMode,
      model: ctx.selectedModel,
      codingAgent: ctx.codingAgent,
    });
    if (!item) return;
    enqueuePrompt(ctx.chatId, item);
    // Text sent mid-turn goes into that turn at its next tool call (it stays queued
    // until then, and runs as the next turn if this one ends first).
    if (ctx.agentRunning && item.text && !item.attachments.length) {
      void getApi()?.steer_message?.(ctx.chatId, item.id, item.text);
    }
    if (!keepDraft) ctx.setInputText("");
    if (!overrideText) ctx.clearAttachments();
    return;
  }

  if (!overrideText && !ctx.canSend) return;
  if (!keepDraft) ctx.setInputText("");
  if (!overrideText) ctx.clearAttachments();
  ctx.dispatchSend(text, apiAttachments, ctx.agentMode, ctx.selectedModel, ctx.codingAgent);
}

export interface ChatComposerProps {
  chat: ChatTab;
  allChats: ChatTab[];
  visible: boolean;
  isPopup: boolean;
  paneHost: HTMLDivElement | null;
  textareaRef: RefObject<HTMLDivElement>;
  inputText: string;
  setInputText: Dispatch<SetStateAction<string>>;
  caret: number;
  setCaret: Dispatch<SetStateAction<number>>;
  /** Ctrl+Z / Ctrl+Y in the composer; true when the key was a history key. */
  onHistoryKey: (event: KeyboardEvent<HTMLDivElement>) => boolean;
  isDragOver: boolean;
  changesOpen: boolean;
  onToggleChanges: () => void;
  onCloseChanges: () => void;
  liveVoice: boolean;
  liveVoiceHandlers: LiveVoiceUiHandlers | null;
  onLiveVoiceChange: (live: boolean, handlers: LiveVoiceUiHandlers | null) => void;
  agentMode: AgentMode;
  setAgentMode: Dispatch<SetStateAction<AgentMode>>;
  selectedModel: string;
  setSelectedModel: Dispatch<SetStateAction<string>>;
  codingAgent: string;
  setCodingAgent: Dispatch<SetStateAction<string>>;
  thinkingEffort: string;
  setThinkingEffort: Dispatch<SetStateAction<string>>;
  modelPickerSignal: number;
  onPickModel: () => void;
  onModelMetaChange: (meta: { supportsVision: boolean; name: string }) => void;
  showDefaultModelCta: boolean;
  pickModelFirst: boolean;
  notConnected: { provider: string; label: string } | null;
  llmProviders: ReturnType<typeof usePluginContributions>["llm_providers"];
  heldForModel: number;
  noModelText: string;
  visionBlocked: boolean;
  attachmentError: Attachments["error"];
  displayModelLabel: string;
  slashStatus: string;
  availableCommands: SlashCommand[];
  runSlashCommand: (command: SlashCommand, argument: string) => void;
  attachments: Attachments["attachments"];
  readInParts: Attachments["readInParts"];
  removeAttachment: Attachments["removeAttachment"];
  retryVideo: Attachments["retryVideo"];
  updateAttachmentImage: Attachments["updateAttachmentImage"];
  addFiles: Attachments["addFiles"];
  clearAttachments: Attachments["clearAttachments"];
  contextUsage?: ContextUsage;
  sessionFiles: SessionFile[];
  contextPanelOpen: boolean;
  onToggleContextPanel: () => void;
  onCloseContextPanel: () => void;
  onContextChanged: () => void;
  onOpenFile: OpenFile;
  onOpenChat: (chat: ChatTab) => void;
  groupMemberCount: number;
  agentRunning: boolean;
  noModelsAvailable: boolean;
  modelsUnavailable: boolean;
  streamBuffer: string;
  hasContent: boolean;
  canQueue: boolean;
  canSend: boolean;
  onSend: (text?: string) => void;
  onStop: () => void;
}

const TOP_RESIZE_MARGIN = 16;
const MIN_MESSAGE_AREA_HEIGHT = 100;

/** The chat's input: notices, attachments, the draft with its @ / ~ picker, and the toolbar. */
export function ChatComposer({
  chat,
  allChats,
  visible,
  isPopup,
  paneHost,
  textareaRef,
  inputText,
  setInputText,
  caret,
  setCaret,
  onHistoryKey,
  isDragOver,
  changesOpen,
  onToggleChanges,
  onCloseChanges,
  liveVoice,
  liveVoiceHandlers,
  onLiveVoiceChange,
  agentMode,
  setAgentMode,
  selectedModel,
  setSelectedModel,
  codingAgent,
  setCodingAgent,
  thinkingEffort,
  setThinkingEffort,
  modelPickerSignal,
  onPickModel,
  onModelMetaChange,
  showDefaultModelCta,
  pickModelFirst,
  notConnected,
  llmProviders,
  heldForModel,
  noModelText,
  visionBlocked,
  attachmentError,
  displayModelLabel,
  slashStatus,
  availableCommands,
  runSlashCommand,
  attachments,
  readInParts,
  removeAttachment,
  retryVideo,
  updateAttachmentImage,
  addFiles,
  clearAttachments,
  contextUsage,
  sessionFiles,
  contextPanelOpen,
  onToggleContextPanel,
  onCloseContextPanel,
  onContextChanged,
  onOpenFile,
  onOpenChat,
  groupMemberCount,
  agentRunning,
  noModelsAvailable,
  modelsUnavailable,
  streamBuffer,
  hasContent,
  canQueue,
  canSend,
  onSend,
  onStop,
}: ChatComposerProps) {
  const [isFocused, setIsFocused] = useState(false);
  const [slashActiveIndex, setSlashActiveIndex] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const [mentionsOn, setMentionsOn] = useState(true);
  const [slashRefsOn, setSlashRefsOn] = useState(true);
  const queuedNote = useSyncExternalStore(subscribeComposerNotes, () => composerNotes.get(chat.id) ?? "");
  useEffect(() => {
    if (!queuedNote) return;
    const t = window.setTimeout(() => setComposerNote(chat.id, ""), 5000);
    return () => window.clearTimeout(t);
  }, [queuedNote, chat.id]);
  const inputBoxRef = useRef<HTMLDivElement>(null);
  const abortDictationRef = useRef<(() => void) | null>(null);
  const send = (text?: string) => {
    abortDictationRef.current?.();
    onSend(text);
  };
  const composerTargetRef = useUiTarget("chat.composer", {
    kind: "chat",
    label: "Chat composer",
    route: "chat",
  });
  const inputTargetRef = useUiTarget("chat.composer.input", {
    kind: "input",
    label: "Message",
    route: "chat",
  });
  const sendTargetRef = useUiTarget("chat.composer.send", {
    kind: "button",
    label: "Send",
    route: "chat",
  });
  const inputBoxMergedRef = useMergedRef(inputBoxRef, composerTargetRef);
  const textareaMergedRef = useMergedRef(textareaRef, inputTargetRef);
  const minTextareaHeight = useRef(60);
  const maxTextareaHeightRef = useRef(Infinity);
  const liveTextareaHeightRef = useRef<number | null>(null);
  const inputResizingRef = useRef(false);
  const [textareaHeight, setTextareaHeight] = useState<number | null>(null);

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
    if (att.kind === "video") {
      return { kind: "video", name: att.name, mime: att.mime, media_url: att.previewUrl };
    }
    return { kind: "file", name: att.name, mime: att.mime, text: att.text };
  }, [attachments, previewAttachmentId]);

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
  }, [textareaRef]);

  const refreshMaxTextareaHeight = useCallback(() => {
    const max = computeMaxTextareaHeight();
    maxTextareaHeightRef.current = max;
    const el = textareaRef.current;
    if (el) {
      el.style.maxHeight = Number.isFinite(max) ? `${max}px` : "";
    }
    return max;
  }, [computeMaxTextareaHeight, textareaRef]);

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
  }, [textareaRef]);

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

  // The saved height goes on the draft's element here: a style prop on a component is
  // lint-forbidden, and the drag and grow paths already size it through the ref.
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el || textareaHeight == null) return;
    const max = maxTextareaHeightRef.current;
    el.style.height = `${textareaHeight}px`;
    el.style.minHeight = `${minTextareaHeight.current}px`;
    el.style.maxHeight = Number.isFinite(max) ? `${max}px` : "";
  }, [textareaHeight, textareaRef]);

  const onInputResizeEnd = useCallback(() => {
    inputResizingRef.current = false;
    const h = liveTextareaHeightRef.current;
    if (h != null) setTextareaHeight(h);
  }, []);

  // Right-click the composer's buttons: a switch per button, hidden ones included (saved
  // with the header's, ids `composer:<button>`). Send and Stop always stay.
  const composerMenu = useContextMenuState<null>();
  const { isVisible: barBtnVisible, setVisible: setBarBtnVisible } = useHeaderVisibility();
  const composerBtnShown = (id: string) => barBtnVisible(`composer:${id}`);
  const composerMenuItems: ContextMenuItem[] = COMPOSER_BUTTONS.map((btn) => {
    const on = composerBtnShown(btn.id);
    return {
      id: btn.id,
      label: btn.label,
      icon: <btn.Icon />,
      switch: true,
      checked: on,
      keepOpen: true,
      onClick: () => setBarBtnVisible(`composer:${btn.id}`, !on),
    };
  });

  // Shift+Enter (or a long line wrapping) grows the box to show what's written, up to the
  // same cap the handle has; after a send it goes back to the height it had before.
  const heightBeforeGrowRef = useRef<number | null>(null);
  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el || isPopup || inputResizingRef.current) return;
    if (!inputText) {
      const before = heightBeforeGrowRef.current;
      heightBeforeGrowRef.current = null;
      if (before != null) applyTextareaHeight(before, true);
      return;
    }
    const overflow = el.scrollHeight - el.clientHeight;
    if (overflow <= 1) return;
    refreshMaxTextareaHeight();
    const current = liveTextareaHeightRef.current ?? el.offsetHeight;
    if (heightBeforeGrowRef.current == null) heightBeforeGrowRef.current = current;
    applyTextareaHeight(current + overflow, true);
  }, [inputText, isPopup, applyTextareaHeight, refreshMaxTextareaHeight, textareaRef]);

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
    // The floor comes from the stylesheet, never from a height set here: re-measuring
    // offsetHeight each time the tab showed again raised the floor to the current
    // height, so after one expand the composer filled the pane and hid every message.
    const inlineMin = el.style.minHeight;
    el.style.minHeight = "";
    const cssMin = parseFloat(getComputedStyle(el).minHeight);
    el.style.minHeight = inlineMin;
    const measured = cssMin > 0 ? cssMin : el.offsetHeight;
    if (measured > 0) {
      minTextareaHeight.current = measured;
      refreshMaxTextareaHeight();
      applyTextareaHeight(liveTextareaHeightRef.current ?? measured, true);
    }
  }, [visible, isPopup, refreshMaxTextareaHeight, applyTextareaHeight, textareaRef]);

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

  useEffect(() => {
    if (isPopup) return;
    refreshMaxTextareaHeight();
    const current = liveTextareaHeightRef.current;
    if (current == null) return;
    // Imperative only — commit:true here can ResizeObserver↔setState loop (React #185).
    applyTextareaHeight(current, false);
  }, [isPopup, attachments.length, visionBlocked, attachmentError, refreshMaxTextareaHeight, applyTextareaHeight]);

  const slashQuery = readSlashQuery(inputText);
  const slashMatches = useMemo(
    () => (slashQuery === null ? [] : filterCommands(availableCommands, slashQuery)),
    [availableCommands, slashQuery],
  );
  const caretToken = readCaretToken(inputText, caret);
  // Nothing loads on mount or chat switch: the first @, / or ~ typed loads the shared catalog.
  const { mentions, slashRefs, files, loading: refsLoading } = useChatReferenceCatalog(
    REF_TRIGGER.test(inputText),
    chat.id,
  );
  const showMentions = mentionsOn && caretToken?.trigger === "@";
  const showSlashRefs = slashRefsOn && caretToken?.trigger === "/";
  const fileMatches = useFileRefMatches(caretToken?.query ?? "", showSlashRefs, files);
  const showSearch = (mentionsOn || slashRefsOn) && caretToken?.trigger === "~";
  const searchQuery = showSearch ? caretToken?.query ?? "" : "";
  const searchHits = useInnerRefSearch(searchQuery, showSearch, files, slashRefs);
  const refMatches = useMemo(() => {
    if (refsLoading && (showMentions || showSlashRefs)) return [LOADING_REF];
    if (showSearch) return mergeInnerRefs(instantInnerRefs(searchQuery, mentions, slashRefs, files), searchHits);
    const pool = showMentions ? mentions : showSlashRefs ? slashRefs : [];
    const named = filterChatRefs(pool, caretToken?.query ?? "");
    if (!showSlashRefs || !(caretToken?.query ?? "").trim()) return named;
    return [...named, ...fileMatches.slice(0, 12)];
  }, [refsLoading, showSearch, searchQuery, searchHits, showMentions, showSlashRefs, mentions, slashRefs, files, fileMatches, caretToken?.query]);
  const pickerCommands = slashQuery === null ? [] : slashMatches;
  const pickerCount = pickerCommands.length + refMatches.length;
  const slashMenuOpen = !slashDismissed && pickerCount > 0;
  const pickerKind = caretToken?.trigger ?? "";

  useEffect(() => {
    setSlashDismissed(false);
  }, [pickerKind]);

  useEffect(() => {
    setSlashActiveIndex(0);
  }, [pickerKind, slashQuery, caretToken?.query]);

  useEffect(() => {
    let cancelled = false;
    void getApi()
      ?.get_settings()
      .then((settings) => {
        if (cancelled || !settings) return;
        if (typeof settings.chat_mentions_enabled === "boolean") {
          setMentionsOn(settings.chat_mentions_enabled);
        }
        if (typeof settings.chat_slash_references_enabled === "boolean") {
          setSlashRefsOn(settings.chat_slash_references_enabled);
        }
      })
      .catch(() => {});
    const onToggle = (event: Event) => {
      const detail = (event as CustomEvent<{ mentions?: boolean; slashRefs?: boolean }>).detail;
      if (typeof detail?.mentions === "boolean") setMentionsOn(detail.mentions);
      if (typeof detail?.slashRefs === "boolean") setSlashRefsOn(detail.slashRefs);
    };
    window.addEventListener("ducky:chat-ref-toggles", onToggle);
    return () => {
      cancelled = true;
      window.removeEventListener("ducky:chat-ref-toggles", onToggle);
    };
  }, []);

  const insertChatReference = useCallback(
    (ref: ChatRef) => {
      if (ref.id === LOADING_REF.id) return;
      const el = textareaRef.current;
      const caretNow = el && document.activeElement === el ? composerCaret(el) : caret;
      const token = readCaretToken(inputText, caretNow);
      if (!token || token.trigger !== ref.trigger) return;
      const next = insertChatRef(inputText, token, ref.label, ref.href);
      setInputText(next.text);
      setCaret(next.caret);
      requestAnimationFrame(() => {
        const node = textareaRef.current;
        node?.focus();
        composerSetCaret(node, next.caret);
      });
    },
    [inputText, caret],
  );

  /** Menu pick: commands taking an argument only get completed, not run. */
  const completeSlashCommand = useCallback(
    (command: SlashCommand) => {
      if (command.requiresArgument) {
        setInputText(`/${command.name} `);
        textareaRef.current?.focus();
        return;
      }
      setInputText("");
      runSlashCommand(command, "");
    },
    [runSlashCommand],
  );

  const sendBtnTitle = showDefaultModelCta
    ? "Connect an AI in Settings → LLMs"
    : pickModelFirst
      ? "Pick a model — your message sends then"
      : canQueue
      ? "Queue follow-up (runs when this turn finishes)"
      : canSend
        ? codingAgent !== "ducky"
          ? `Send via ${codingAgent}`
          : "Send"
        : "Add API key, model, and message or attachments";

  const sendBtnClass = showDefaultModelCta
    ? "chat-pane-send-btn chat-pane-send-btn--settings-cta"
    : hasContent
      ? canSend || canQueue || pickModelFirst
        ? "chat-pane-send-btn chat-pane-send-btn--has-text-can-send"
        : "chat-pane-send-btn chat-pane-send-btn--has-text-no-send"
      : "chat-pane-send-btn";

  return (
        <div className="chat-pane-composer-host">
          {slashMenuOpen ? (
            <SlashCommandMenu
              commands={pickerCommands}
              refs={refMatches}
              activeIndex={slashActiveIndex}
              onHover={setSlashActiveIndex}
              onSelect={completeSlashCommand}
              onSelectRef={insertChatReference}
            />
          ) : null}
        <div
          ref={inputBoxMergedRef}
          className={`no-drag chat-pane-input-box${isFocused ? " chat-pane-input-box--focused" : ""}${isDragOver ? " chat-pane-input-box--drag-over" : ""}${liveVoice ? " chat-pane-input-box--voice" : ""}${changesOpen && !chat.isGroup ? " chat-pane-input-box--ledger" : ""}`}
        >
          {!chat.isGroup ? (
            <ChatChangesSlide
              open={changesOpen}
              convId={chat.id}
              allChats={allChats}
              onOpenFile={onOpenFile}
              onOpenChat={onOpenChat}
              host={paneHost}
              onClose={onCloseChanges}
            />
          ) : null}
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
                  onStopSpeaking={liveVoiceHandlers.onStopSpeaking}
                  onRetry={liveVoiceHandlers.onRetry}
                  autoSend={liveVoiceHandlers.autoSend}
                  setAutoSend={liveVoiceHandlers.setAutoSend}
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
                  setMuted={liveVoiceHandlers.setMuted}
                  chatModel={selectedModel}
                  setChatModel={setSelectedModel}
                  codingAgent={codingAgent}
                  setCodingAgent={setCodingAgent}
                  showChatModel={!chat.isGroup}
                />
              ) : null}
            </div>
          </div>

          <div className="chat-pane-composer-pane chat-pane-composer-pane--type">
            {showDefaultModelCta || pickModelFirst || notConnected ? (
              <ModelSetupNotice
                state={
                  showDefaultModelCta
                    ? { kind: "no_ai", gateways: llmProviders }
                    : pickModelFirst
                      ? { kind: "pick", held: heldForModel, why: noModelText }
                      : { kind: "not_connected", ...notConnected! }
                }
                onPickModel={onPickModel}
              />
            ) : (
              (visionBlocked || attachmentError) && (
                <div className={`composer-attach-warning${visionBlocked ? " is-vision-blocked" : ""}`}>
                  {visionBlocked
                    ? `${displayModelLabel} cannot use images — switch to a vision model or remove images.`
                    : attachmentError}
                </div>
              )
            )}
            {slashStatus ? <div className="slash-command-status">{slashStatus}</div> : null}
            {queuedNote ? <div className="slash-command-status" role="status">{queuedNote}</div> : null}
            <ComposerAttachmentChips
              attachments={attachments}
              readInParts={readInParts}
              onRemove={removeAttachment}
              onPreview={(att) => setPreviewAttachmentId(att.id)}
              onRetry={retryVideo}
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
            <ComposerDraft
              inputRef={textareaMergedRef}
              value={inputText}
              caret={caret}
              onChange={(value, nextCaret) => {
                setInputText(value);
                setCaret(nextCaret);
              }}
              onCaret={setCaret}
              onFocus={() => setIsFocused(true)}
              onBlur={() => setIsFocused(false)}
              onKeyDown={(e) => {
                if (onHistoryKey(e)) return;
                if (slashMenuOpen) {
                  const count = pickerCount;
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setSlashActiveIndex((i) => (i + 1) % count);
                    return;
                  }
                  if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setSlashActiveIndex((i) => (i - 1 + count) % count);
                    return;
                  }
                  if (e.key === "Escape") {
                    e.preventDefault();
                    setSlashDismissed(true);
                    return;
                  }
                  if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
                    e.preventDefault();
                    if (slashActiveIndex < pickerCommands.length) {
                      const picked = pickerCommands[slashActiveIndex];
                      if (picked) completeSlashCommand(picked);
                    } else {
                      const picked = refMatches[slashActiveIndex - pickerCommands.length];
                      if (picked) insertChatReference(picked);
                    }
                    return;
                  }
                }
                if (e.key === "Tab") {
                  e.preventDefault();
                  return;
                }
                if (e.key === "Enter" && !e.shiftKey) {
                  if (enterInsertsNewline()) return;
                  e.preventDefault();
                  send();
                }
              }}
              placeholder={composerPlaceholder({
                liveVoiceMuted: Boolean(liveVoice && liveVoiceHandlers?.muted),
                isGroup: Boolean(chat.isGroup),
                groupEmpty: groupMemberCount === 0,
                agentRunning,
                noModelsAvailable,
                modelLabel: displayModelLabel,
                mentionsEnabled: mentionsOn,
              })}
              className="chat-pane-textarea"
            />
          </div>

          <div className="chat-pane-input-toolbar" onContextMenu={(e) => composerMenu.open(e, null)}>
            <div className="chat-pane-input-toolbar-left">
              {/* One button for the chat's mode (Ask / Plan / Agent) and its permissions. */}
              {!chat.isGroup && composerBtnShown("permissions") ? (
                <ChatPermissionsButton
                  convId={chat.id}
                  codingAgent={codingAgent}
                  agentMode={agentMode}
                  setAgentMode={setAgentMode}
                />
              ) : null}
              {!chat.isGroup && composerBtnShown("context") ? (
                <ContextMeter
                  usedTokens={contextUsage?.used_tokens ?? 0}
                  contextLimit={contextUsage?.context_limit ?? 128000}
                  inputTokens={contextUsage?.input_tokens}
                  outputTokens={contextUsage?.output_tokens}
                  usage={contextUsage}
                  sessionFiles={sessionFiles}
                  convId={chat.id}
                  omitted={contextUsage?.omitted}
                  agentMode={agentMode}
                  model={selectedModel}
                  agentRunning={agentRunning}
                  panelOpen={contextPanelOpen}
                  onTogglePanel={onToggleContextPanel}
                  onClosePanel={onCloseContextPanel}
                  onOpenFile={onOpenFile}
                  onContextChanged={onContextChanged}
                  onClearDraft={() => {
                    setInputText("");
                    clearAttachments();
                  }}
                />
              ) : null}
              {!chat.isGroup && composerBtnShown("model") ? (
                <>
                  <div className="chat-pane-model-effort">
                    <ModelSelector
                      selectedModel={selectedModel}
                      setSelectedModel={setSelectedModel}
                      codingAgent={codingAgent}
                      setCodingAgent={setCodingAgent}
                      convId={chat.id}
                      preserveSelection
                      openSignal={modelPickerSignal}
                      onModelMetaChange={onModelMetaChange}
                      uiTarget="chat.composer.model"
                      thinkingEffort={thinkingEffort}
                      onEffortChange={setThinkingEffort}
                    />
                  </div>
                </>
              ) : null}
              {!chat.isGroup && composerBtnShown("changes") ? (
                <ChatChangesButton open={changesOpen} onClick={onToggleChanges} />
              ) : null}
            </div>

            <div className="chat-pane-input-toolbar-right">
              {composerBtnShown("snip") ? (
                <SnipButton
                  chatId={chat.id}
                  disabled={modelsUnavailable}
                  onCaptured={(file, meta) =>
                    void addFiles([file], {
                      imagesOnly: true,
                      projectPath: meta?.projectPath,
                    })
                  }
                />
              ) : null}
              {composerBtnShown("attach") ? (
                <AttachMenuButton
                  disabled={modelsUnavailable}
                  onAddFiles={(files) => void addFiles(files)}
                />
              ) : null}
              {composerBtnShown("voice") ? (
              <VoiceControls
                chatId={chat.id}
                disabled={
                  modelsUnavailable ||
                  (Boolean(chat.isGroup) && groupMemberCount === 0)
                }
                inputText={inputText}
                setInputText={setInputText}
                onSend={send}
                abortDictationRef={abortDictationRef}
                onDictationEnd={(text) => {
                  setCaret(text.length);
                  requestAnimationFrame(() => textareaRef.current?.focus({ preventScroll: true }));
                }}
                streamText={streamBuffer}
                agentRunning={agentRunning}
                duckyVoice={chat.ttsVoice}
                duckySpeed={chat.ttsSpeed}
                isGroup={Boolean(chat.isGroup)}
                onLiveChange={onLiveVoiceChange}
              />
              ) : null}
              <span ref={sendTargetRef} className="chat-pane-send-target">
                {agentRunning ? (
                  hasContent ? (
                    <button
                      type="button"
                      onClick={() => send()}
                      disabled={!canQueue}
                      title={sendBtnTitle}
                      className={sendBtnClass}
                    >
                      <Icons.Send />
                    </button>
                  ) : null
                ) : (
                  <button
                    type="button"
                    onClick={() => send()}
                    disabled={!showDefaultModelCta && !pickModelFirst && !canSend}
                    title={sendBtnTitle}
                    className={sendBtnClass}
                  >
                    {showDefaultModelCta ? <Icons.Settings /> : <Icons.Send />}
                  </button>
                )}
              </span>
              {agentRunning ? (
                <button type="button" onClick={onStop} className="chat-pane-stop-btn">
                  <div className="chat-pane-stop-btn-icon" />
                </button>
              ) : null}
            </div>
          </div>
          {composerMenu.menu ? (
            <ContextMenu
              x={composerMenu.menu.x}
              y={composerMenu.menu.y}
              items={composerMenuItems}
              onClose={composerMenu.close}
            />
          ) : null}
        </div>
        </div>
  );
}
