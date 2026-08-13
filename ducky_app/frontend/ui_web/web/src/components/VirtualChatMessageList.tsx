import {
  forwardRef,
  memo,
  useImperativeHandle,
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

import { groupChatRowsIntoTurns, type ChatRow } from "../utils/chatMessageGroups";

import { ChatCollapseScopeProvider } from "../hooks/useChatCollapseState";

import { ChatPlanPopup } from "./ChatPlanPopup";

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
    const [scrollerReady, setScrollerReady] = useState(0);

    /**
     * Whether we auto-tail the bottom — the browser/terminal behavior: pinned to
     * the newest content while it streams in. Flips to false the instant the user
     * scrolls up, and back to true when they return to the bottom, so an in-flight
     * turn never yanks a scrolled-up reader back down.
     */
    const followingRef = useRef(true);

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

    const renderItem = useCallback((row: ChatRow): ReactNode => {
      const scope = String(row.id);
      if (row.kind === "bubble" && row.role === "user") {
        const isEditable = editableRowId != null && row.id === editableRowId;
        return (
          <ChatCollapseScopeProvider scope={scope}>
            <div className="virtual-chat-message-list-query" data-chat-row-id={row.id}>
              <EditableUserMessage
                text={row.text}
                attachments={row.attachments}
                editable={isEditable}
                currentMode={composerMode}
                currentModel={composerModel}
                codingAgent={composerCodingAgent}
                setCodingAgent={setComposerCodingAgent}
                convId={convId}
                onResend={onResend}
                onStop={isEditable ? onStop : undefined}
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
            convId={convId}
            captureKeys={captureAskKeys}
            onOpenChat={onOpenChat}
            onStopLinked={onStopLinked}
            onStop={onStop}
            onOpenFile={onOpenFile}
            allChats={allChats}
            liveLinkedAgents={linkedAgents}
            externalAgent={composerCodingAgent !== "ducky"}
          />
        );
      } else if (row.kind === "activity") {
        body = (
          <AgentActivityGroup
            items={row.items}
            author={row.author}
            convId={convId}
            captureAskKeys={captureAskKeys}
            onOpenChat={onOpenChat}
            onStopLinked={onStopLinked}
            onStop={onStop}
            onOpenFile={onOpenFile}
            allChats={allChats}
            liveLinkedAgents={linkedAgents}
            externalAgent={composerCodingAgent !== "ducky"}
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
            onOpenFile={onOpenFile}
            onStop={onStop}
            showSpeakButton={row.id === lastSpeakRowId}
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
    }, [
      editableRowId,
      composerMode,
      composerModel,
      composerCodingAgent,
      setComposerCodingAgent,
      convId,
      captureAskKeys,
      onResend,
      onStop,
      onOpenChat,
      onStopLinked,
      onOpenFile,
      allChats,
      linkedAgents,
      lastSpeakRowId,
    ]);

    // Release / re-engage the tail purely from the user's scroll position, so one
    // small scroll up is enough to break free and streaming can't fight it back.
    useEffect(() => {
      const scroller = scrollerElRef.current;
      if (!scroller) return;
      const onScroll = () => {
        const atBottom = distanceFromBottom() <= AT_BOTTOM_THRESHOLD_PX;
        followingRef.current = atBottom;
        onAtBottomChange(atBottom);
      };
      scroller.addEventListener("scroll", onScroll, { passive: true });
      return () => scroller.removeEventListener("scroll", onScroll);
    }, [scrollerReady, distanceFromBottom, onAtBottomChange]);

    // Plain browser scrolling: append/stream content only follows while the user
    // remains at the bottom. No virtual measurement state, hidden item list, or
    // synthetic padding is involved.
    useEffect(() => {
      if (!followingRef.current) return;
      const frame = window.requestAnimationFrame(() => {
        scrollToBottom("auto");
      });
      return () => window.cancelAnimationFrame(frame);
    }, [
      rows,
      showActivityPanel,
      activityHeaderOnly,
      activityStatusText,
      activityLines,
      isWaitingOnLinked,
      waitingLinked,
      askSession,
      scrollToBottom,
    ]);

    const handleJumpToLatestClick = useCallback(() => {
      followingRef.current = true;
      onJumpToLatest();
    }, [onJumpToLatest]);

    const turns = useMemo(() => groupChatRowsIntoTurns(rows), [rows]);

    // Finished plans stay under the turns that created/updated them (scroll history).
    // Active plans are docked above the composer in ChatPane — not in this scroller.
    const planTurnIds = useMemo(() => {
      if (!planAllDone || chatPlan == null) return null;
      const ids = new Set<string>();
      const isPlanTool = (name: string | undefined) =>
        name === "ducky_create_plan" || name === "ducky_update_plan";
      for (const turn of turns) {
        for (const row of turn.responses) {
          if (row.kind === "tool" && isPlanTool(row.intent.tool?.name)) {
            ids.add(turn.id);
            break;
          }
          if (row.kind === "activity") {
            const hit = row.items.some(
              (item) => item.kind === "tool" && isPlanTool(item.intent.tool?.name),
            );
            if (hit) {
              ids.add(turn.id);
              break;
            }
          }
        }
      }
      return ids;
    }, [turns, planAllDone, chatPlan]);

    return (
      <div
        className="virtual-chat-message-list-root"
        {...(translateMessages ? {} : { "data-no-translate": "" })}
      >
        <div
          ref={setScrollerRef}
          className="virtual-chat-message-list-scroller"
        >
          {turns.map((turn) => (
            <div key={turn.id} className="virtual-chat-turn" data-chat-turn-id={turn.id}>
              {turn.query ? (
                <div
                  key={`row-${turn.query.id}`}
                  className="virtual-chat-message-list-row virtual-chat-turn-query"
                >
                  {renderItem(turn.query)}
                  {chatPlan != null && planTurnIds?.has(turn.id) ? (
                    <ChatPlanPopup
                      plan={chatPlan}
                      progress={chatPlanProgress}
                      onOpenPlan={onOpenPlan}
                      onStopTracking={onStopTrackingPlan}
                      onOpenFile={onOpenFile}
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
                      {renderItem(row)}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
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
                  onComplete={(result) => settleAskUser(result, askSession.id)}
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
    );
  },
));
