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

import { ChatPlanPopup } from "./ChatPlanPopup";
import { ConversationScrollPeek } from "./ConversationScrollPeek";

import { AskUserForm } from "../ask-user";
import type { AskUserSession } from "../ask-user";
import { settleAskUser } from "../ask-user";

import { getApi } from "../hooks/usePanelApi";

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
const CHUNK_TURNS = 8;
const OVERSCAN_CHUNKS = 2;
/** First-paint stand-in until a chunk is measured. Over-estimate so the thumb
 *  only grows as real heights land — never shrinks from back-fill. */
const ESTIMATE_CHUNK_PX = 3200;

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
  // jsdom / first layout: no viewport yet — keep everything so tests and
  // the first paint before measure do not flash an empty pane.
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

/** Report list commits slower than this (one 60 Hz frame) to the perf log. */
const SLOW_COMMIT_MS = 16;
const SLOW_COMMIT_REPORT_INTERVAL_MS = 1000;

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
        onStop={env.onStop}
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
        onStop={env.onStop}
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
    const applyWindowRef = useRef<() => void>(() => {});
    const [win, setWin] = useState<{ start: number; end: number }>(() =>
      tailWindow(Math.ceil(groupChatRowsIntoTurns(rows).length / CHUNK_TURNS)),
    );
    const [padTick, setPadTick] = useState(0);

    const distanceFromBottom = useCallback(() => {
      const el = scrollerElRef.current;
      if (!el) return 0;
      return el.scrollHeight - el.scrollTop - el.clientHeight;
    }, []);

    const scrollToBottom = useCallback((behavior: "auto" | "smooth" = "auto") => {
      const scroller = scrollerElRef.current;
      if (!scroller) return;
      scroller.scrollTo({ top: scroller.scrollHeight, behavior });
    }, []);

    const setScrollerRef = useCallback((node: HTMLDivElement | null) => {
      if (scrollerElRef.current === node) return;
      scrollerElRef.current = node;
      if (node) setScrollerReady((n) => n + 1);
    }, []);

    useImperativeHandle(ref, () => ({
      scrollToLatest: () => {
        followingRef.current = true;
        requestAnimationFrame(() => scrollToBottom("auto"));
      },
    }));

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

    // Release / re-engage the tail purely from the user's scroll position, so one
    // small scroll up is enough to break free and streaming can't fight it back.
    // Coalesced to one scrollHeight read per frame — the raw scroll event fires
    // many times per frame and each read forces layout.
    useEffect(() => {
      const scroller = scrollerElRef.current;
      if (!scroller) return;
      let frame = 0;
      let lastAtBottom: boolean | null = null;
      const onScroll = () => {
        if (frame) return;
        frame = window.requestAnimationFrame(() => {
          frame = 0;
          const atBottom = distanceFromBottom() <= AT_BOTTOM_THRESHOLD_PX;
          followingRef.current = atBottom;
          if (atBottom !== lastAtBottom) {
            lastAtBottom = atBottom;
            onAtBottomChange(atBottom);
          }
          applyWindowRef.current();
        });
      };
      scroller.addEventListener("scroll", onScroll, { passive: true });
      return () => {
        scroller.removeEventListener("scroll", onScroll);
        if (frame) window.cancelAnimationFrame(frame);
      };
    }, [scrollerReady, distanceFromBottom, onAtBottomChange]);

    // Auto-follow on real tail growth: a ResizeObserver on the content (and the
    // scroller, for pane resizes) fires once per actual height change, after
    // layout — instead of a scrollTo on every prop change every frame.
    useEffect(() => {
      const scroller = scrollerElRef.current;
      const content = contentElRef.current;
      if (!scroller || !content || typeof ResizeObserver === "undefined") return;
      const observer = new ResizeObserver(() => {
        if (followingRef.current) scrollToBottom("auto");
        applyWindowRef.current();
      });
      observer.observe(content);
      observer.observe(scroller);
      return () => observer.disconnect();
    }, [scrollerReady, scrollToBottom]);

    const handleJumpToLatestClick = useCallback(() => {
      followingRef.current = true;
      onJumpToLatest();
    }, [onJumpToLatest]);

    const jumpPeek = useCallback(
      (top: number) => {
        const scroller = scrollerElRef.current;
        if (!scroller) return;
        followingRef.current = false;
        onAtBottomChange(false);
        scroller.scrollTo({ top, behavior: "auto" });
      },
      [onAtBottomChange],
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
      let next = el
        ? chatListWindowRange(el.scrollTop, el.clientHeight, heights, OVERSCAN_CHUNKS)
        : tailWindow(n);
      if (followingRef.current) {
        const distance = el ? el.scrollHeight - el.scrollTop - el.clientHeight : Infinity;
        next = distance > AT_BOTTOM_THRESHOLD_PX ? tailWindow(n) : { start: next.start, end: n - 1 };
      }
      setWin((prev) => (sameWindow(prev, next) ? prev : next));
    }, []);
    applyWindowRef.current = applyWindow;

    const onChunkHeight = useCallback(
      (index: number, height: number) => {
        const h = heightsRef.current;
        if (index >= h.length || h[index] === height || height <= 0) return;
        const delta = height - h[index];
        let startY = 0;
        for (let i = 0; i < index; i++) startY += h[i];
        h[index] = height;
        const scroller = scrollerElRef.current;
        if (scroller && !followingRef.current && startY + height - delta <= scroller.scrollTop) {
          scroller.scrollTop += delta;
        }
        setPadTick((t) => t + 1);
        applyWindow();
      },
      [applyWindow],
    );

    useLayoutEffect(() => {
      heightsRef.current = [];
      ensureHeights(chunks.length);
      setWin(tailWindow(chunks.length));
    }, [convId]); // eslint-disable-line react-hooks/exhaustive-deps -- reset only on chat switch

    useLayoutEffect(() => {
      ensureHeights(chunks.length);
      if (followingRef.current) scrollToBottom("auto");
      applyWindow();
    }, [chunks.length, scrollerReady, ensureHeights, applyWindow, scrollToBottom]);

    useEffect(() => {
      if (chunks.length === 0) return;
      const mounted = Math.max(0, win.end - win.start + 1);
      console.log(
        `[chat-list] window ${win.start}-${win.end}/${chunks.length} mounted=${mounted} turns=${turns.length}`,
      );
    }, [convId, win.start, win.end, chunks.length, turns.length]);

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

    // #region agent log
    // Commit-time probe (works in production builds, unlike <Profiler>): render
    // start → layout effect ≈ render + reconcile of the whole list subtree.
    // Reported to the perf log only when a commit misses a frame, at most once
    // per second, with the row/turn counts so regressions show their scale.
    const renderStart = performance.now();
    const lastReportRef = useRef(0);
    useLayoutEffect(() => {
      const dt = performance.now() - renderStart;
      if (dt < SLOW_COMMIT_MS) return;
      const now = performance.now();
      if (now - lastReportRef.current < SLOW_COMMIT_REPORT_INTERVAL_MS) return;
      lastReportRef.current = now;
      try {
        void getApi()?.report_ui_perf([
          {
            kind: "dbg_render",
            name: "chat_list_commit",
            duration_ms: Math.round(dt),
            rows: rows.length,
            turns: turns.length,
            streaming: rows.length > 0 && rows[rows.length - 1].id === "stream",
          },
        ]);
      } catch {
        /* ignore */
      }
    });
    // #endregion

    return (
      <ChatRowEnvContext.Provider value={env}>
        <div
          className="virtual-chat-message-list-root"
          {...(translateMessages ? {} : { "data-no-translate": "" })}
        >
          <div
            ref={setScrollerRef}
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
