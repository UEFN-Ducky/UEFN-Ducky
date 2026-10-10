import { toolActivityId, useToolActivityTarget } from "../navigation/toolActivity";
import {
  createContext,
  forwardRef,
  memo,
  useContext,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  useCallback,
  useEffect,
  useMemo,
  type ReactNode,
} from "react";

import { Icons } from "../icons/Icons";

import { AgentActivityPanel } from "./AgentActivityPanel";

import { MessageBubble } from "./MessageBubble";

import { EditableUserMessage } from "./EditableUserMessage";
import { AutomatedPromptMessage } from "./AutomatedPromptMessage";
import { parseAutomatedPrompt } from "../utils/automatedPrompt";

import { ToolExecutionCard } from "./ToolExecutionCard";

import { AgentActivityGroup } from "./AgentActivityGroup";

import type { ActivityLine } from "../utils/agentActivity";

import {
  groupChatRowsIntoTurns,
  reconcileTurns,
  type ChatRow,
  type ChatTurn,
} from "../utils/chatMessageGroups";

import { ChatCollapseScopeProvider } from "../hooks/useChatCollapseState";
import { DOCK_LAYOUT_IDLE_EVENT, dockLayoutBusy } from "../workspace/dockLayoutEvents";

import { ChatPlanPopup } from "./ChatPlanPopup";
import { ConversationScrollPeek } from "./ConversationScrollPeek";

import { AskUserForm } from "../ask-user";
import type { AskUserSession } from "../ask-user";
import { settleAskUser } from "../ask-user";

import type { AgentMode, ChatPlan, ChatTab, LinkedAgent, MessageAttachmentDto, PlanProgress } from "../types/panel";

export interface VirtualChatMessageListHandle {
  scrollToLatest: () => void;
}

interface VirtualChatMessageListProps {
  rows: ChatRow[];
  showActivityPanel: boolean;
  activityHeaderOnly: boolean;
  activityStatusText?: string;
  /** Show frozen "Took Xm" footer after the turn finishes. */
  showIdleTurnTimer?: boolean;
  isWaitingOnLinked: boolean;
  waitingLinked: LinkedAgent[];
  isAtBottom: boolean;
  hasNewBelow: boolean;
  /** Id of the last user row — the only one that can be edited/resent. */
  editableRowId: string | null;
  composerMode: AgentMode;
  composerModel: string;
  setComposerModel: (id: string) => void;
  composerCodingAgent: string;
  setComposerCodingAgent: (id: string) => void;
  convId: string;
  /** Bind ask-user number/Enter shortcuts only for the focused visible pane. */
  captureAskKeys?: boolean;
  /** Live ask-user questionnaire — rendered inside the scrollable chat, not the composer dock. */
  askSession?: AskUserSession | null;
  onResend: (text: string, mode: AgentMode, model: string, attachments?: MessageAttachmentDto[]) => void;
  /** Stop the live run (shown on sticky last question + collapsed live headers). */
  onStop?: () => void;
  /** Resume the last interrupted assistant turn in place. */
  onContinue?: () => void;
  onAtBottomChange: (atBottom: boolean) => void;
  onJumpToLatest: () => void;
  onOpenChat: (chat: ChatTab) => void;
  onStopLinked: (childConvId: string) => void;
  onOpenFile?: (path: string, name: string, options?: { line?: number }) => void;
  allChats: ChatTab[];
  linkedAgents: LinkedAgent[];
  duckyStyle?: string;
  activityLines: ActivityLine[];
  chatPlan: ChatPlan | null;
  chatPlanProgress: PlanProgress | null;
  planAllDone: boolean;
  onOpenPlan?: () => void;
  onStopTrackingPlan?: () => void | Promise<void>;
  /** When true, UI language may translate message text (per-chat hover toggle). */
  translateMessages?: boolean;
}

/** Fixed breathing room below the last turn. */
const BOTTOM_SPACER_MIN_PX = 24;

/** Distance from scroll bottom that still counts as "pinned to bottom". */
const AT_BOTTOM_THRESHOLD_PX = 24;

/**
 * Turns per windowing chunk. Off-screen chunks are unmounted and replaced
 * with a height spacer so the scrollbar stays full-size. Boundaries are
 * index-based, so older chunks never change membership when new turns arrive.
 */
// A turn can contain a whole report. Eight turns per chunk plus two chunks of
// overscan kept up to 40 reports mounted, even in a modest 17-turn conversation.
const CHUNK_TURNS = 1;
const OVERSCAN_CHUNKS = 1;
/** First-paint stand-in until a chunk is measured. Over-estimate so the thumb
 *  only grows as real heights land — never shrinks from back-fill. */
const ESTIMATE_CHUNK_PX = 800;

export function resetChatListMountCache(): void {
  /* height cache is per-list instance; kept so existing tests can call it */
}

/** Visible chunk index range for a scroll position, plus `overscan` on each side. */
export function chatListWindowRange(
  scrollTop: number,
  clientHeight: number,
  heights: readonly number[],
  overscan = OVERSCAN_CHUNKS,
): { start: number; end: number } {
  const n = heights.length;
  if (n === 0) return { start: 0, end: -1 };
  // Unknown viewport: the *range helper* covers the whole list. applyWindow
  // must not use this on first paint — clientHeight 0 would mount every chunk.
  if (clientHeight <= 0) return { start: 0, end: n - 1 };

  const offsets = new Array<number>(n + 1);
  offsets[0] = 0;
  for (let i = 0; i < n; i++) offsets[i + 1] = offsets[i] + heights[i];
  const total = offsets[n];
  const viewTop = Math.max(0, Math.min(scrollTop, total));
  const viewBottom = Math.max(viewTop, Math.min(scrollTop + clientHeight, total));

  let start = 0;
  while (start < n - 1 && offsets[start + 1] <= viewTop) start++;
  let end = start;
  while (end < n - 1 && offsets[end + 1] < viewBottom) end++;

  return {
    start: Math.max(0, start - overscan),
    end: Math.min(n - 1, end + overscan),
  };
}

function tailWindow(n: number, overscan = OVERSCAN_CHUNKS): { start: number; end: number } {
  if (n <= 0) return { start: 0, end: -1 };
  const keep = 1 + overscan * 2;
  return { start: Math.max(0, n - keep), end: n - 1 };
}

function sameWindow(
  a: { start: number; end: number },
  b: { start: number; end: number },
): boolean {
  return a.start === b.start && a.end === b.end;
}

/**
 * Everything a row needs that is NOT per-row data. Lives in context so the
 * memoized turn/row views take only their own row as a prop: a streamed delta
 * re-renders exactly the turn that grew, never the whole history.
 */
interface ChatRowEnv {
  convId: string;
  captureAskKeys: boolean;
  composerMode: AgentMode;
  composerModel: string;
  setComposerModel: (id: string) => void;
  composerCodingAgent: string;
  setComposerCodingAgent: (id: string) => void;
  onResend: VirtualChatMessageListProps["onResend"];
  onStop?: () => void;
  onContinue?: () => void;
  onOpenChat: (chat: ChatTab) => void;
  onStopLinked: (childConvId: string) => void;
  onOpenFile?: VirtualChatMessageListProps["onOpenFile"];
  allChats: ChatTab[];
  linkedAgents: LinkedAgent[];
  chatPlan: ChatPlan | null;
  chatPlanProgress: PlanProgress | null;
  onOpenPlan?: () => void;
  onStopTrackingPlan?: () => void | Promise<void>;
}

const ChatRowEnvContext = createContext<ChatRowEnv | null>(null);

function useChatRowEnv(): ChatRowEnv {
  const env = useContext(ChatRowEnvContext);
  if (!env) throw new Error("ChatRowEnvContext missing");
  return env;
}

interface ChatRowViewProps {
  row: ChatRow;
  /** This user row is the last one and may be edited/resent. */
  editable: boolean;
  /** Play-audio control — only the latest assistant text bubble. */
  showSpeakButton: boolean;
  /** Offer "Continue" on this interrupted bubble. */
  showContinue: boolean;
}

/** One chat row. Memoized on row identity: history rows never re-render while streaming. */
const ChatRowView = memo(function ChatRowView({
  row,
  editable,
  showSpeakButton,
  showContinue,
}: ChatRowViewProps) {
  const env = useChatRowEnv();
  const scope = String(row.id);
  const externalAgent = env.composerCodingAgent !== "ducky";

  if (row.kind === "bubble" && row.role === "user") {
    // Turns another agent or Ducky started: show who sent them, and never offer to edit them.
    const automated = parseAutomatedPrompt(row.text);
    if (automated) {
      return (
        <ChatCollapseScopeProvider scope={scope}>
          <div className="virtual-chat-message-list-query" data-chat-row-id={row.id}>
            <AutomatedPromptMessage prompt={automated} onStop={editable ? env.onStop : undefined} />
          </div>
        </ChatCollapseScopeProvider>
      );
    }
    return (
      <ChatCollapseScopeProvider scope={scope}>
        <div className="virtual-chat-message-list-query" data-chat-row-id={row.id}>
          <EditableUserMessage
            text={row.text}
            attachments={row.attachments}
            editable={editable}
            currentMode={env.composerMode}
            currentModel={env.composerModel}
            codingAgent={env.composerCodingAgent}
            setCodingAgent={env.setComposerCodingAgent}
            convId={env.convId}
            onResend={env.onResend}
            onStop={editable ? env.onStop : undefined}
          />
        </div>
      </ChatCollapseScopeProvider>
    );
  }

  let body: ReactNode;
  if (row.kind === "tool") {
    body = (
      <ToolExecutionCard
        intent={row.intent}
        result={row.result}
        convId={env.convId}
        captureKeys={env.captureAskKeys}
        onOpenChat={env.onOpenChat}
        onStopLinked={env.onStopLinked}
        onOpenFile={env.onOpenFile}
        allChats={env.allChats}
        liveLinkedAgents={env.linkedAgents}
        externalAgent={externalAgent}
      />
    );
  } else if (row.kind === "activity") {
    body = (
      <AgentActivityGroup
        items={row.items}
        author={row.author}
        convId={env.convId}
        captureAskKeys={env.captureAskKeys}
        onOpenChat={env.onOpenChat}
        onStopLinked={env.onStopLinked}
        onOpenFile={env.onOpenFile}
        allChats={env.allChats}
        liveLinkedAgents={env.linkedAgents}
        externalAgent={externalAgent}
      />
    );
  } else {
    body = (
      <MessageBubble
        role={row.role}
        text={row.text}
        isStreaming={row.isStreaming}
        thinking={row.thinking}
        incomplete={row.incomplete}
        error={row.error}
        author={row.author}
        voiceId={row.author?.tts_voice}
        speed={row.author?.tts_speed}
        onOpenFile={env.onOpenFile}
        onStop={env.onStop}
        onContinue={showContinue ? env.onContinue : undefined}
        selectedModel={env.composerModel}
        setSelectedModel={env.setComposerModel}
        codingAgent={env.composerCodingAgent}
        setCodingAgent={env.setComposerCodingAgent}
        convId={env.convId}
        showSpeakButton={showSpeakButton}
      />
    );
  }

  return (
    <ChatCollapseScopeProvider scope={scope}>
      <div className="virtual-chat-message-list-item" data-chat-row-id={row.id}>
        {body}
      </div>
    </ChatCollapseScopeProvider>
  );
});

interface ChatTurnViewProps {
  turn: ChatTurn;
  /** The query of this turn is the editable (last) user message. */
  queryEditable: boolean;
  /** Row id inside this turn that owns the speak button, if any. */
  speakRowId: string | null;
  /** Row id inside this turn that owns the "Continue" affordance, if any. */
  continueRowId: string | null;
  /** Finished plan popup docks under this turn's query. */
  showPlan: boolean;
}

/**
 * One user query (sticky) + its reply rows. Memoized on the turn object, and
 * `reconcileTurns` keeps turn identity stable across frames, so during a run
 * only the last turn re-renders — and inside it only the row that changed.
 */
const ChatTurnView = memo(function ChatTurnView({
  turn,
  queryEditable,
  speakRowId,
  continueRowId,
  showPlan,
}: ChatTurnViewProps) {
  const env = useChatRowEnv();
  const [planOpen, setPlanOpen] = useState(false);
  const plan = showPlan ? env.chatPlan : null;
  const queryClass =
    "virtual-chat-message-list-row virtual-chat-turn-query" +
    (plan ? " has-plan" : "") +
    (plan && planOpen ? " is-plan-open" : "");

  return (
    <div className="virtual-chat-turn" data-chat-turn-id={turn.id}>
      {turn.query ? (
        <div className={queryClass}>
          <ChatRowView
            row={turn.query}
            editable={queryEditable}
            showSpeakButton={false}
            showContinue={false}
          />
          {plan ? (
            <ChatPlanPopup
              plan={plan}
              progress={env.chatPlanProgress}
              onOpenPlan={env.onOpenPlan}
              onStopTracking={env.onStopTrackingPlan}
              onOpenFile={env.onOpenFile}
              onOpenChange={setPlanOpen}
            />
          ) : null}
        </div>
      ) : null}
      {turn.responses.length > 0 ? (
        <div className="virtual-chat-turn-response" data-chat-turn-response={turn.id}>
          {turn.responses.map((row) => (
            <div
              key={`row-${row.id}${row.kind === "bubble" && row.isStreaming ? "-s" : ""}`}
              className="virtual-chat-message-list-row"
            >
              <ChatRowView
                row={row}
                editable={false}
                showSpeakButton={row.id === speakRowId}
                showContinue={row.id === continueRowId}
              />
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
});

/** Index of the last turn whose responses contain `rowId` (reverse scan, early exit). */
function turnIndexOfResponse(turns: ChatTurn[], rowId: string | null): number {
  if (rowId == null) return -1;
  for (let t = turns.length - 1; t >= 0; t--) {
    const responses = turns[t].responses;
    for (let i = responses.length - 1; i >= 0; i--) {
      if (responses[i].id === rowId) return t;
    }
  }
  return -1;
}

function turnIndexOfQuery(turns: ChatTurn[], rowId: string | null): number {
  if (rowId == null) return -1;
  for (let t = turns.length - 1; t >= 0; t--) {
    if (turns[t].query?.id === rowId) return t;
  }
  return -1;
}

const MeasuredChunk = memo(function MeasuredChunk({
  index,
  onHeight,
  children,
}: {
  index: number;
  onHeight: (index: number, height: number) => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const report = () => onHeight(index, el.offsetHeight);
    report();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(report);
    observer.observe(el);
    return () => observer.disconnect();
  }, [index, onHeight]);
  return (
    <div ref={ref} className="virtual-chat-chunk" data-chat-chunk={index}>
      {children}
    </div>
  );
});

const isPlanTool = (name: string | undefined) =>
  name === "ducky_create_plan" || name === "ducky_update_plan";

function turnHasPlanTool(turn: ChatTurn): boolean {
  for (const row of turn.responses) {
    if (row.kind === "tool" && isPlanTool(row.intent.tool?.name)) return true;
    if (row.kind === "activity") {
      if (row.items.some((item) => item.kind === "tool" && isPlanTool(item.intent.tool?.name))) {
        return true;
      }
    }
  }
  return false;
}

export const VirtualChatMessageList = memo(forwardRef<VirtualChatMessageListHandle, VirtualChatMessageListProps>(
  function VirtualChatMessageList(
    {
      rows,
      showActivityPanel,
      activityHeaderOnly,
      activityStatusText = "",
      showIdleTurnTimer = false,
      isWaitingOnLinked,
      waitingLinked,
      isAtBottom,
      hasNewBelow,
      editableRowId,
      composerMode,
      composerModel,
      setComposerModel,
      composerCodingAgent,
      setComposerCodingAgent,
      convId,
      captureAskKeys = true,
      askSession = null,
      onResend,
      onStop,
      onContinue,
      onAtBottomChange,
      onJumpToLatest,
      onOpenChat,
      onStopLinked,
      onOpenFile,
      allChats,
      linkedAgents,
      duckyStyle,
      activityLines,
      chatPlan,
      chatPlanProgress,
      planAllDone,
      onOpenPlan,
      onStopTrackingPlan,
      translateMessages = false,
    },
    ref,
  ) {
    const scrollerElRef = useRef<HTMLElement | null>(null);
    const contentElRef = useRef<HTMLDivElement | null>(null);
    const [scrollerReady, setScrollerReady] = useState(0);

    /**
     * Whether we auto-tail the bottom — the browser/terminal behavior: pinned to
     * the newest content while it streams in. Flips to false the instant the user
     * scrolls up, and back to true when they return to the bottom, so an in-flight
     * turn never yanks a scrolled-up reader back down.
     */
    const followingRef = useRef(true);
    const heightsRef = useRef<number[]>([]);
    const hadViewportRef = useRef(false);
    const applyWindowRef = useRef<() => void>(() => {});
    const [win, setWin] = useState<{ start: number; end: number }>(() =>
      tailWindow(Math.ceil(groupChatRowsIntoTurns(rows).length / CHUNK_TURNS)),
    );
    const [padTick, setPadTick] = useState(0);

    const inputDirectionRef = useRef(0);
    const inputHeightRef = useRef(0);
    const scrollPositionRef = useRef({ top: 0, height: 0, viewport: 0 });
    const followFrameRef = useRef(0);

    const setFollowing = useCallback((following: boolean) => {
      if (!following && followFrameRef.current) {
        cancelAnimationFrame(followFrameRef.current);
        followFrameRef.current = 0;
      }
      if (followingRef.current === following) return;
      followingRef.current = following;
      onAtBottomChange(following);
    }, [onAtBottomChange]);

    const scrollToBottom = useCallback(() => {
      const scroller = scrollerElRef.current;
      if (!scroller || !followingRef.current || scroller.clientHeight <= 0 || dockLayoutBusy()) return;
      const height = scroller.scrollHeight;
      scroller.scrollTo({ top: height, behavior: "auto" });
      // Our own scroll (including a clamp after content shrinks) is not user input.
      scrollPositionRef.current = { top: scroller.scrollTop, height, viewport: scroller.clientHeight };
    }, []);

    const followLatest = useCallback(() => {
      inputDirectionRef.current = 0;
      setFollowing(true);
      if (followFrameRef.current) cancelAnimationFrame(followFrameRef.current);
      followFrameRef.current = requestAnimationFrame(() => {
        followFrameRef.current = 0;
        scrollToBottom();
        applyWindowRef.current();
      });
    }, [setFollowing, scrollToBottom]);

    const setScrollerRef = useCallback((node: HTMLDivElement | null) => {
      if (scrollerElRef.current === node) return;
      scrollerElRef.current = node;
      if (node) setScrollerReady((n) => n + 1);
    }, []);

    useImperativeHandle(ref, () => ({ scrollToLatest: followLatest }), [followLatest]);
    useEffect(() => { onAtBottomChange(followingRef.current); }, [scrollerReady, onAtBottomChange]);
    useEffect(() => () => { if (followFrameRef.current) cancelAnimationFrame(followFrameRef.current); }, []);

    // Play-audio only on the latest assistant text bubble — not every mid-turn status line.
    const lastSpeakRowId = useMemo(() => {
      for (let i = rows.length - 1; i >= 0; i--) {
        const row = rows[i];
        if (row.kind === "bubble" && row.role === "assistant" && row.text?.trim()) {
          return row.id;
        }
      }
      return null;
    }, [rows]);

    const lastIncompleteRowId = useMemo(() => {
      for (let i = rows.length - 1; i >= 0; i--) {
        const row = rows[i];
        if (row.kind === "bubble" && row.incomplete && !row.isStreaming) {
          return row.id;
        }
      }
      return null;
    }, [rows]);

    // Release following before the browser scrolls or measures lazy content.
    // Only deliberate downward input can re-engage it; layout/anchoring scroll
    // events must never reinterpret a reader's position as permission to follow.
    useEffect(() => {
      const scroller = scrollerElRef.current;
      if (!scroller) return;
      let frame = 0;
      let touchY: number | null = null;
      let draggingScrollbar = false;
      const intent = (direction: number) => {
        inputDirectionRef.current = direction;
        inputHeightRef.current = scroller.scrollHeight;
        if (direction < 0) setFollowing(false);
        else if (direction > 0 && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= 1) setFollowing(true);
      };
      const onWheel = (event: WheelEvent) => {
        if (!event.ctrlKey && event.deltaY) intent(Math.sign(event.deltaY));
      };
      const onTouchStart = (event: TouchEvent) => { touchY = event.touches.length === 1 ? event.touches[0].clientY : null; };
      const onTouchMove = (event: TouchEvent) => {
        if (touchY === null || event.touches.length !== 1) return;
        const nextY = event.touches[0].clientY;
        if (nextY !== touchY) intent(Math.sign(touchY - nextY));
        touchY = nextY;
      };
      const onTouchEnd = () => { touchY = null; };
      const onKeyDown = (event: KeyboardEvent) => {
        if (event.defaultPrevented || event.altKey || event.metaKey) return;
        if (event.target instanceof Element && event.target.closest('input, textarea, select, button, [contenteditable="true"], [role="textbox"]')) return;
        if (["ArrowUp", "PageUp", "Home"].includes(event.key) || (event.key === " " && event.shiftKey)) intent(-1);
        else if (["ArrowDown", "PageDown", "End", " "].includes(event.key)) intent(1);
      };
      const onPointerDown = (event: PointerEvent) => {
        if (event.target !== scroller || event.button !== 0) return;
        const bounds = scroller.getBoundingClientRect();
        if (event.clientX < bounds.right - Math.max(12, bounds.width - scroller.clientWidth)) return;
        draggingScrollbar = true;
        setFollowing(false);
        inputDirectionRef.current = 0;
        inputHeightRef.current = scroller.scrollHeight;
      };
      const onPointerUp = () => { draggingScrollbar = false; };
      const onScroll = () => {
        if (scroller.clientHeight <= 0) return;
        const top = scroller.scrollTop;
        const previous = scrollPositionRef.current;
        if (draggingScrollbar && top !== previous.top) intent(Math.sign(top - previous.top));
        // Scrollbar/assistive scrolling can arrive without wheel or key events.
        // Detect moving up synchronously, but exclude native layout clamping.
        if (followingRef.current && top < previous.top - 0.5 && scroller.scrollHeight === previous.height && scroller.clientHeight === previous.viewport && previous.height - top - previous.viewport > AT_BOTTOM_THRESHOLD_PX) {
          inputDirectionRef.current = -1;
          setFollowing(false);
        }
        scrollPositionRef.current = { ...previous, top };
        if (frame) return;
        frame = window.requestAnimationFrame(() => {
          frame = 0;
          if (scroller.clientHeight <= 0) return;
          const height = scroller.scrollHeight;
          if (!followingRef.current && inputDirectionRef.current > 0 && height === inputHeightRef.current && height - scroller.scrollTop - scroller.clientHeight <= AT_BOTTOM_THRESHOLD_PX) setFollowing(true);
          scrollPositionRef.current = { top: scroller.scrollTop, height, viewport: scroller.clientHeight };
          applyWindowRef.current();
        });
      };
      scroller.addEventListener("wheel", onWheel, { passive: true });
      scroller.addEventListener("touchstart", onTouchStart, { passive: true });
      scroller.addEventListener("touchmove", onTouchMove, { passive: true });
      scroller.addEventListener("touchend", onTouchEnd, { passive: true });
      scroller.addEventListener("touchcancel", onTouchEnd, { passive: true });
      scroller.addEventListener("keydown", onKeyDown);
      scroller.addEventListener("pointerdown", onPointerDown, { passive: true });
      window.addEventListener("pointerup", onPointerUp, { passive: true });
      window.addEventListener("pointercancel", onPointerUp, { passive: true });
      scroller.addEventListener("scroll", onScroll, { passive: true });
      return () => {
        scroller.removeEventListener("wheel", onWheel);
        scroller.removeEventListener("touchstart", onTouchStart);
        scroller.removeEventListener("touchmove", onTouchMove);
        scroller.removeEventListener("touchend", onTouchEnd);
        scroller.removeEventListener("touchcancel", onTouchEnd);
        scroller.removeEventListener("keydown", onKeyDown);
        scroller.removeEventListener("pointerdown", onPointerDown);
        window.removeEventListener("pointerup", onPointerUp);
        window.removeEventListener("pointercancel", onPointerUp);
        scroller.removeEventListener("scroll", onScroll);
        if (frame) window.cancelAnimationFrame(frame);
      };
    }, [scrollerReady, setFollowing]);

    // Auto-follow on real tail growth: a ResizeObserver on the content (and the
    // scroller, for pane resizes) fires once per actual height change, after
    // layout — instead of a scrollTo on every prop change every frame.
    useEffect(() => {
      const scroller = scrollerElRef.current;
      const content = contentElRef.current;
      if (!scroller || !content || typeof ResizeObserver === "undefined") return;
      const observer = new ResizeObserver(() => {
        if (dockLayoutBusy() || scroller.clientHeight <= 0) return;
        if (followingRef.current) scrollToBottom();
        else {
          inputDirectionRef.current = 0;
          scrollPositionRef.current = { top: scroller.scrollTop, height: scroller.scrollHeight, viewport: scroller.clientHeight };
        }
        applyWindowRef.current();
      });
      observer.observe(content);
      observer.observe(scroller);
      return () => observer.disconnect();
    }, [scrollerReady, scrollToBottom]);

    const handleJumpToLatestClick = useCallback(() => {
      followLatest();
      onJumpToLatest();
    }, [followLatest, onJumpToLatest]);

    const jumpPeek = useCallback(
      (top: number) => {
        const scroller = scrollerElRef.current;
        if (!scroller) return;
        inputDirectionRef.current = 0;
        setFollowing(false);
        scroller.scrollTo({ top, behavior: "auto" });
      },
      [setFollowing],
    );

    // Turn objects keep their identity across frames unless a row inside them
    // changed, so the memoized ChatTurnView skips every untouched turn.
    const prevTurnsRef = useRef<ChatTurn[]>([]);
    const turns = useMemo(() => {
      const next = reconcileTurns(prevTurnsRef.current, groupChatRowsIntoTurns(rows));
      prevTurnsRef.current = next;
      return next;
    }, [rows]);

    const chunks = useMemo(() => {
      const out: ChatTurn[][] = [];
      for (let i = 0; i < turns.length; i += CHUNK_TURNS) out.push(turns.slice(i, i + CHUNK_TURNS));
      return out;
    }, [turns]);

    const ensureHeights = useCallback((n: number) => {
      const h = heightsRef.current;
      while (h.length < n) h.push(ESTIMATE_CHUNK_PX);
      if (h.length > n) h.length = n;
    }, []);
    ensureHeights(chunks.length);

    const applyWindow = useCallback(() => {
      const heights = heightsRef.current;
      const n = heights.length;
      if (n === 0) {
        setWin((prev) => (sameWindow(prev, { start: 0, end: -1 }) ? prev : { start: 0, end: -1 }));
        return;
      }
      const el = scrollerElRef.current;
      // A hidden tab has no viewport. Preserve its measured window and reader
      // position instead of replacing it with the tail. First mount is already
      // initialized to a small tail window, so this never mounts all history.
      if (!el || el.clientHeight <= 0) {
        if (!hadViewportRef.current) {
          const next = tailWindow(n);
          setWin((prev) => (sameWindow(prev, next) ? prev : next));
        }
        return;
      }
      hadViewportRef.current = true;
      let next = chatListWindowRange(el.scrollTop, el.clientHeight, heights, OVERSCAN_CHUNKS);
      if (followingRef.current) {
        const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
        next = distance > AT_BOTTOM_THRESHOLD_PX ? tailWindow(n) : { start: next.start, end: n - 1 };
      }
      setWin((prev) => (sameWindow(prev, next) ? prev : next));
    }, []);
    applyWindowRef.current = applyWindow;

    const onChunkHeight = useCallback(
      (index: number, height: number) => {
        if (dockLayoutBusy()) return;
        const h = heightsRef.current;
        if (index >= h.length || h[index] === height || height <= 0) return;
        h[index] = height;
        // Native anchoring owns corrections above the reader. Adjusting
        // scrollTop here as well double-applies lazy height changes.
        setPadTick((t) => t + 1);
        applyWindow();
      },
      [applyWindow],
    );

    useEffect(() => {
      const flush = () => {
        if (!scrollerElRef.current?.clientHeight) return;
        const root = contentElRef.current;
        if (root) {
          root.querySelectorAll<HTMLElement>("[data-chat-chunk]").forEach((node) => {
            const index = Number(node.dataset.chatChunk);
            if (!Number.isFinite(index)) return;
            const height = node.offsetHeight;
            const h = heightsRef.current;
            if (index >= h.length || height <= 0) return;
            h[index] = height;
          });
        }
        setPadTick((t) => t + 1);
        if (followingRef.current) scrollToBottom();
        applyWindowRef.current();
      };
      window.addEventListener(DOCK_LAYOUT_IDLE_EVENT, flush);
      return () => window.removeEventListener(DOCK_LAYOUT_IDLE_EVENT, flush);
    }, [scrollToBottom]);

    useLayoutEffect(() => {
      heightsRef.current = [];
      hadViewportRef.current = false;
      ensureHeights(chunks.length);
      setWin(tailWindow(chunks.length));
    }, [convId]);  

    useLayoutEffect(() => {
      ensureHeights(chunks.length);
      if (followingRef.current) scrollToBottom();
      applyWindow();
    }, [chunks.length, scrollerReady, ensureHeights, applyWindow, scrollToBottom]);

    const toolTarget = useToolActivityTarget(convId);
    const handledToolTarget = useRef(0);
    useEffect(() => {
      if (!toolTarget || handledToolTarget.current === toolTarget.serial) return;
      let index = -1;
      for (let i = turns.length - 1; i >= 0; i--) {
        if (turns[i].responses.some((row) => row.kind === "tool"
          ? toolActivityId(row.intent.tool) === toolTarget.toolId
          : row.kind === "activity" && row.items.some((item) => item.kind === "tool" && toolActivityId(item.intent.tool) === toolTarget.toolId))) {
          index = i; break;
        }
      }
      if (index < 0) return; // History may still be loading.
      handledToolTarget.current = toolTarget.serial;
      const chunk = Math.floor(index / CHUNK_TURNS);
      followingRef.current = false;
      const scroller = scrollerElRef.current;
      if (scroller) scroller.scrollTop = heightsRef.current.slice(0, chunk).reduce((a, b) => a + b, 0);
      setWin({ start: Math.max(0, chunk - 1), end: Math.min(chunks.length - 1, chunk + 1) });
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (handledToolTarget.current !== toolTarget.serial) return;
          const nodes = contentElRef.current?.querySelectorAll<HTMLElement>("[data-tool-activity-id]");
          const node = Array.from(nodes || []).reverse().find((el) => el.dataset.toolActivityId === toolTarget.toolId);
          node?.scrollIntoView?.({ block: "center" });
        });
      });
    }, [toolTarget, turns, chunks.length]);

    const { topPad, bottomPad } = useMemo(() => {
      void padTick;
      const h = heightsRef.current;
      let top = 0;
      let bottom = 0;
      for (let i = 0; i < win.start; i++) top += h[i] ?? ESTIMATE_CHUNK_PX;
      for (let i = win.end + 1; i < chunks.length; i++) bottom += h[i] ?? ESTIMATE_CHUNK_PX;
      return { topPad: top, bottomPad: bottom };
    }, [win.start, win.end, chunks.length, padTick]);

    const editableTurnIdx = useMemo(() => turnIndexOfQuery(turns, editableRowId), [turns, editableRowId]);
    const speakTurnIdx = useMemo(() => turnIndexOfResponse(turns, lastSpeakRowId), [turns, lastSpeakRowId]);
    const continueTurnIdx = useMemo(
      () => turnIndexOfResponse(turns, lastIncompleteRowId),
      [turns, lastIncompleteRowId],
    );

    // Finished plans stay under the turns that created/updated them (scroll history).
    // Active plans are docked above the composer in ChatPane — not in this scroller.
    const planTurnIds = useMemo(() => {
      if (!planAllDone || chatPlan == null) return null;
      const ids = new Set<string>();
      for (const turn of turns) {
        if (turnHasPlanTool(turn)) ids.add(turn.id);
      }
      return ids;
    }, [turns, planAllDone, chatPlan]);

    const env = useMemo<ChatRowEnv>(
      () => ({
        convId,
        captureAskKeys,
        composerMode,
        composerModel,
        setComposerModel,
        composerCodingAgent,
        setComposerCodingAgent,
        onResend,
        onStop,
        onContinue,
        onOpenChat,
        onStopLinked,
        onOpenFile,
        allChats,
        linkedAgents,
        chatPlan,
        chatPlanProgress,
        onOpenPlan,
        onStopTrackingPlan,
      }),
      [
        convId,
        captureAskKeys,
        composerMode,
        composerModel,
        setComposerModel,
        composerCodingAgent,
        setComposerCodingAgent,
        onResend,
        onStop,
        onContinue,
        onOpenChat,
        onStopLinked,
        onOpenFile,
        allChats,
        linkedAgents,
        chatPlan,
        chatPlanProgress,
        onOpenPlan,
        onStopTrackingPlan,
      ],
    );

    const askSessionId = askSession?.id;
    const handleAskComplete = useCallback(
      (result: Parameters<typeof settleAskUser>[0]) => {
        if (askSessionId) settleAskUser(result, askSessionId);
      },
      [askSessionId],
    );

    return (
      <ChatRowEnvContext.Provider value={env}>
        <div
          className="virtual-chat-message-list-root"
          {...(translateMessages ? {} : { "data-no-translate": "" })}
        >
          <div
            ref={setScrollerRef}
            tabIndex={0}
            role="region"
            aria-label="Conversation messages"
            className="virtual-chat-message-list-scroller"
            data-chat-window={`${win.start}-${win.end}`}
          >
            <div ref={contentElRef} className="virtual-chat-message-list-content">
              {topPad > 0 ? (
                <div className="virtual-chat-window-spacer" style={{ height: topPad }} aria-hidden />
              ) : null}
              {chunks.map((chunk, c) =>
                c < win.start || c > win.end ? null : (
                  <MeasuredChunk key={`chunk-${c}`} index={c} onHeight={onChunkHeight}>
                    {chunk.map((turn, j) => {
                      const i = c * CHUNK_TURNS + j;
                      return (
                        <ChatTurnView
                          key={turn.id}
                          turn={turn}
                          queryEditable={i === editableTurnIdx}
                          speakRowId={i === speakTurnIdx ? lastSpeakRowId : null}
                          continueRowId={i === continueTurnIdx ? lastIncompleteRowId : null}
                          showPlan={planTurnIds?.has(turn.id) ?? false}
                        />
                      );
                    })}
                  </MeasuredChunk>
                ),
              )}
              {bottomPad > 0 ? (
                <div className="virtual-chat-window-spacer" style={{ height: bottomPad }} aria-hidden />
              ) : null}
              {askSession ? (
                <div
                  className="virtual-chat-message-list-row virtual-chat-ask-row"
                  data-ask-session={askSession.id}
                >
                  <div className="virtual-chat-ask-panel">
                    <AskUserForm
                      questions={askSession.questions}
                      title={askSession.title}
                      queueAhead={askSession.queueAhead}
                      author={askSession.author}
                      captureKeys={captureAskKeys}
                      showDismiss
                      onComplete={handleAskComplete}
                    />
                  </div>
                </div>
              ) : null}
              <div className="virtual-chat-message-list-footer">
                {showActivityPanel ? (
                  <AgentActivityPanel
                    lines={activityLines}
                    duckyStyle={duckyStyle}
                    headerOnly={activityHeaderOnly || showIdleTurnTimer}
                    isWaitingOnLinked={isWaitingOnLinked}
                    waitingTitle={waitingLinked.length === 1 ? waitingLinked[0].title : undefined}
                    waitingCount={waitingLinked.length}
                    autoExpand={!showIdleTurnTimer}
                    statusText={activityStatusText}
                    chatId={convId}
                    showIdleTimer={showIdleTurnTimer}
                  />
                ) : null}
                <div
                  className="virtual-chat-message-list-footer-spacer"
                  style={{ height: BOTTOM_SPACER_MIN_PX }}
                />
              </div>
            </div>
          </div>
          <ConversationScrollPeek
            turns={turns}
            chunkHeights={heightsRef.current}
            turnsPerChunk={CHUNK_TURNS}
            scroller={scrollerElRef.current}
            heightsTick={padTick}
            onJump={jumpPeek}
          />
          {!isAtBottom ? (
            <button
              type="button"
              onClick={handleJumpToLatestClick}
              title={hasNewBelow ? "New messages — jump to latest" : "Scroll to bottom"}
              aria-label={hasNewBelow ? "New messages — jump to latest" : "Scroll to bottom"}
              className={`virtual-chat-message-list-jump-btn${hasNewBelow ? " virtual-chat-message-list-jump-btn--has-new" : ""}`}
            >
              <Icons.ChevronDown />
              {hasNewBelow ? <span className="virtual-chat-message-list-jump-btn-badge" /> : null}
            </button>
          ) : null}
        </div>
      </ChatRowEnvContext.Provider>
    );
  },
));
