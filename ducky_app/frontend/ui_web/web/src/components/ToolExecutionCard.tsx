import { memo, useEffect, useLayoutEffect, useMemo, useState, type KeyboardEvent, type MouseEvent } from "react";
import { getAskUserSessionForConv, subscribeAskUser } from "../ask-user";
import { Icons } from "../icons/Icons";
import { chatCollapseKey, useChatCollapseScope, useChatCollapseState } from "../hooks/useChatCollapseState";
import { ChatListPanel, SingleChatPanel } from "./ChatListPanel";
import { LinkedAgentCard } from "./LinkedAgentCard";
import { ToolFileEditDiff } from "./ToolFileEditDiff";
import { DefaultBody } from "./tool-cards/bodies/DefaultBody";
import { walkthroughStepsFromTool } from "./tool-cards/bodies/WalkthroughBody";
import { resolveToolCategory } from "./tool-cards/toolCategories";
import { runAgentWalkthrough } from "../walkthrough/agentWalkthrough";
import type { ChatMessage, ChatTab, LinkedAgent, ToolCallData } from "../types/panel";
import {
  parseChatListFromToolResult,
  parseListedChatFromToolResult,
  resolveChatTab,
} from "../utils/chatList";
import {
  isChatToolName,
  linkedAgentFromToolResult,
  pendingLinkedFromChatTool,
} from "../utils/linkedAgents";
import { formatToolDuration, humanToolLabel } from "../utils/agentActivity";
import { fmtCompactTokens } from "../utils/contextFormat";
import { unwrapCodingAgentTool } from "../utils/unwrapCodingAgentTool";
import { getApi } from "../hooks/usePanelApi";
import { InlineStopButton } from "./InlineStopButton";

interface ToolExecutionCardProps {
  intent: ChatMessage;
  result: ChatMessage | null;
  /** Owning chat — used to detect a live ask docked above the composer. */
  convId?: string;
  /** When true, this chat pane is visible (unused for ask dock; kept for callers). */
  captureKeys?: boolean;
  onOpenChat?: (chat: ChatTab) => void;
  onStopLinked?: (childConvId: string) => void;
  /** Stop the parent chat run (shown on collapsed live tool headers). */
  onStop?: () => void;
  onOpenFile?: (path: string, name: string, options?: { line?: number }) => void;
  allChats?: ChatTab[];
  liveLinkedAgents?: LinkedAgent[];
  /** Chat runs on a BYOA coding agent — its native tools never touch the UEFN listener. */
  externalAgent?: boolean;
  /** Nested inside an activity accordion — denser chrome, no outer margin. */
  embedded?: boolean;
}

function formatPayload(value: unknown): string {
  if (value === undefined || value === null || value === "") {
    return "";
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return "";
    try {
      return JSON.stringify(JSON.parse(trimmed), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function toolMeta(intent: ChatMessage, result: ChatMessage | null): ToolCallData {
  const done = result?.tool;
  const start = intent.tool;
  const rawArgs = done?.arguments ?? start?.arguments ?? {};
  const unwrapped = unwrapCodingAgentTool(
    done?.name ?? start?.name ?? "tool",
    rawArgs && typeof rawArgs === "object" && !Array.isArray(rawArgs)
      ? (rawArgs as Record<string, unknown>)
      : {},
  );
  return {
    name: unwrapped.name,
    arguments: unwrapped.arguments,
    status: done?.status ?? start?.status ?? (result ? (result.role === "success" ? "success" : "error") : "pending"),
    durationMs: done?.durationMs ?? start?.durationMs,
    result: done?.result ?? start?.result,
    llmTokens: done?.llmTokens ?? start?.llmTokens,
    hint: done?.hint ?? start?.hint,
    fileEdit: done?.fileEdit ?? start?.fileEdit,
  };
}

export const ToolExecutionCard = memo(function ToolExecutionCard({
  intent,
  result,
  convId = "",
  captureKeys: _captureKeys = false,
  onOpenChat,
  onStopLinked,
  onStop,
  onOpenFile,
  allChats = [],
  liveLinkedAgents = [],
  externalAgent = false,
  embedded = false,
}: ToolExecutionCardProps) {
  // #region agent log
  const _renderStart = performance.now();
  useLayoutEffect(() => {
    const _dt = performance.now() - _renderStart;
    if (_dt > 200) {
      try {
        const m = toolMeta(intent, result);
        const rs = typeof m.result === "string" ? m.result.length : JSON.stringify(m.result ?? "").length;
        getApi()?.report_ui_perf([{ kind: "dbg_render", name: `toolcard:${m.name}`, duration_ms: Math.round(_dt), result_len: rs }]);
      } catch { /* ignore */ }
    }
  });
  // #endregion
  const meta = toolMeta(intent, result);
  const isCancelled = meta.status === "cancelled";
  const isRunning = !isCancelled && (!result || meta.status === "pending");
  const isSuccess = meta.status === "success" || result?.role === "success";
  const isChatTool = isChatToolName(meta.name);
  const category = useMemo(() => resolveToolCategory(meta.name), [meta.name]);
  const CategoryIcon = category.icon;
  const Body = category.Body ?? DefaultBody;
  const isAskUser = meta.name === "ducky_ask_user";
  const [askSession, setAskSession] = useState(() =>
    convId ? getAskUserSessionForConv(convId) : null,
  );
  useEffect(() => {
    if (!isAskUser || !convId) {
      setAskSession(null);
      return;
    }
    setAskSession(getAskUserSessionForConv(convId));
    return subscribeAskUser(() => setAskSession(getAskUserSessionForConv(convId)));
  }, [isAskUser, convId]);
  const liveAsk = isAskUser && isRunning && askSession;

  const linkedAgent = useMemo(() => {
    if (!isChatTool) return null;
    const base = isRunning
      ? pendingLinkedFromChatTool(meta.name, meta.arguments, liveLinkedAgents)
      : linkedAgentFromToolResult(meta.name, meta.result);
    if (!base) return null;
    if (!base.childConvId) return base;
    const live = liveLinkedAgents.find((a) => a.childConvId === base.childConvId);
    return live ? { ...base, ...live } : base;
  }, [isRunning, isChatTool, meta.name, meta.result, meta.arguments, liveLinkedAgents]);

  const chatList = useMemo(() => {
    if (isRunning || !isSuccess) return null;
    return parseChatListFromToolResult(meta.name, meta.result);
  }, [isRunning, isSuccess, meta.name, meta.result]);

  const listedChat = useMemo(() => {
    if (isRunning || !isSuccess || chatList) return null;
    return parseListedChatFromToolResult(meta.name, meta.result);
  }, [isRunning, isSuccess, chatList, meta.name, meta.result]);

  const resolveChat = (childConvId: string, title: string): ChatTab =>
    resolveChatTab(childConvId, title, allChats);

  const hideRawResult = Boolean(chatList);
  const fileEdit = !isRunning ? meta.fileEdit : undefined;
  // Any tool with fileEdit meta (workspace_write_file, Cursor Edit/Write/StrReplace, …).
  const showInlineDiff = Boolean(fileEdit);
  const hideArgsForFileEdit = showInlineDiff;
  const walkthroughSteps = !isRunning && meta.name === "ducky_walkthrough_run"
    ? walkthroughStepsFromTool(meta)
    : null;
  const [walkthroughBusy, setWalkthroughBusy] = useState(false);

  const collapseScope = useChatCollapseScope();
  const [expanded, setExpanded] = useChatCollapseState(chatCollapseKey(collapseScope, "tool-card"), false);

  const replayWalkthrough = (e: MouseEvent | KeyboardEvent) => {
    e.stopPropagation();
    if (!walkthroughSteps?.length || walkthroughBusy) return;
    setWalkthroughBusy(true);
    void runAgentWalkthrough(walkthroughSteps).finally(() => setWalkthroughBusy(false));
  };

  // A repeat-guard block isn't a real tool result — render it as a compact "skipped"
  // note (no presenter, no raw toggle, no token badge) instead of a full result card.
  const isGuardBlocked = !isRunning && meta.hint === "repeat-call guard";
  // Guard-blocked calls are benign skips, not failures — render them muted, not red.
  const shellTone = isRunning
    ? "running"
    : isCancelled || isGuardBlocked
      ? "cancelled"
      : isSuccess
        ? "success"
        : "error";
  // Pretty-print JSON only when the card is open — formatting large tool results on
  // every flood of collapsed cards stalls the WebView2 UI thread.
  // Ask-user always needs the result payload so Q&A stays visible without expand.
  const argsText = useMemo(
    () => (expanded || isAskUser ? formatPayload(meta.arguments) : ""),
    [expanded, isAskUser, meta.arguments],
  );
  const resultText = useMemo(
    () => (expanded || isAskUser ? formatPayload(meta.result) : ""),
    [expanded, isAskUser, meta.result],
  );
  const llmTokens = meta.llmTokens ?? 0;
  const tokenSuffix =
    !isRunning && !isGuardBlocked && llmTokens > 0 ? ` · ${fmtCompactTokens(llmTokens)} tok` : "";
  const ms = meta.durationMs ?? 0;
  const relPath = meta.arguments?.relative_path ?? meta.arguments?.path;
  const runningSubtitle = liveAsk
    ? "paused — answer in chat, then Submit…"
    : isAskUser && isRunning
      ? "paused — waiting for Submit…"
      : typeof relPath === "string" && relPath.trim()
        ? relPath.trim().replace(/\\/g, "/")
        : "running…";

  const displayName =
    category.label?.(meta.name) ?? humanToolLabel(meta.name);

  // Chat tools + inline file diffs keep their specialized chrome; category accent
  // only applies to ordinary tool cards.
  const useCategoryChrome = !isChatTool && !showInlineDiff;
  const durationLabel = formatToolDuration(ms);

  return (
    <div className={`tool-execution-card-wrap${embedded ? " tool-execution-card-wrap--embedded" : ""}`}>
      {/* Diff always mounts above the tool bar — never buried behind expand/accordion. */}
      {showInlineDiff && fileEdit ? (
        <ToolFileEditDiff edit={fileEdit} onOpenFile={onOpenFile} />
      ) : null}
      <div
        className={[
          "no-drag selectable-text tool-execution-card-shell",
          `tool-execution-card-shell--${shellTone}`,
          useCategoryChrome ? `tool-execution-card-shell--cat-${category.id}` : "",
          showInlineDiff ? "tool-execution-card-shell--compact" : "",
          embedded ? "tool-execution-card-shell--embedded" : "",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className={`tool-execution-card-toggle${expanded ? " tool-execution-card-toggle--expanded" : ""}`}
        >
          <span className="tool-execution-card-toggle-icon">
            <span className="tool-execution-card-toggle-icon-badge" aria-hidden="true">
              {isRunning ? (
                <span className="tool-execution-card-toggle-icon-badge--spin">
                  <Icons.Spinner />
                </span>
              ) : useCategoryChrome ? (
                <CategoryIcon />
              ) : (
                <Icons.Terminal />
              )}
            </span>
          </span>
          <span className="tool-execution-card-toggle-body">
            <span className="tool-execution-card-toggle-name">
              {displayName}
            </span>
            <span className="tool-execution-card-toggle-status">
              {isRunning && isChatTool && linkedAgent
                ? `waiting for ${linkedAgent.title}…`
                : isRunning
                  ? runningSubtitle
                  : isCancelled
                    ? "canceled"
                    : isGuardBlocked
                      ? "skipped · repeated call"
                      : isSuccess
                        ? `${durationLabel}${tokenSuffix}`
                        : `error · ${durationLabel}${tokenSuffix}`}
            </span>
          </span>
          {walkthroughSteps?.length ? (
            <span
              className="tool-execution-card-replay"
              role="button"
              tabIndex={0}
              title={walkthroughBusy ? "Playing tutorial…" : "Replay tutorial"}
              aria-label="Replay tutorial"
              aria-disabled={walkthroughBusy || undefined}
              onClick={replayWalkthrough}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  replayWalkthrough(e);
                }
              }}
            >
              <Icons.Refresh />
            </span>
          ) : null}
          {isRunning && onStop ? (
            <span
              className="tool-execution-card-stop-slot"
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
            >
              <InlineStopButton onClick={onStop} />
            </span>
          ) : null}
          <span
            className={`tool-execution-card-toggle-chevron${expanded ? " tool-execution-card-toggle-chevron--expanded" : ""}`}
          >
            <Icons.ChevronDown />
          </span>
        </button>

        {linkedAgent && linkedAgent.childConvId && onOpenChat && onStopLinked ? (
          <div className="tool-execution-card-linked-essentials">
            <LinkedAgentCard
              agent={linkedAgent}
              duckyStyle={resolveChat(linkedAgent.childConvId, linkedAgent.title).duckyStyle}
              compact
              onOpen={() => onOpenChat(resolveChat(linkedAgent.childConvId, linkedAgent.title))}
              onStop={() => onStopLinked(linkedAgent.childConvId)}
            />
          </div>
        ) : linkedAgent && isRunning ? (
          <div className="tool-execution-card-linked-essentials">
            <div className="tool-execution-card-waiting-row">
              <div className="status-dot online tool-execution-card-waiting-dot" />
              Waiting for linked chat &ldquo;{linkedAgent.title}&rdquo;… Parent continues when it finishes or you stop it.
            </div>
          </div>
        ) : null}

        {/* Ask-user Q&A stays visible in the chat stream (not behind expand). */}
        {isAskUser && !isCancelled && !isGuardBlocked ? (
          <div className="tool-execution-card-body tool-execution-card-body--ask-user">
            {liveAsk || isRunning ? (
              <div className="tool-execution-card-waiting-row ask-user-tool-hint">
                <div className="status-dot online tool-execution-card-waiting-dot" />
                Agent paused — answer the questionnaire in chat, then Submit to continue.
              </div>
            ) : null}
            <Body
              toolName={meta.name}
              args={meta.arguments}
              argsText={argsText}
              resultText={resultText}
              isSuccess={isSuccess}
              isError={!isSuccess && !isRunning}
              showResult={!isRunning && !hideRawResult}
              hideArgs={false}
              hint={!isRunning ? meta.hint : undefined}
              onOpenFile={onOpenFile}
            />
          </div>
        ) : null}

        <div className={`tool-card-collapse${expanded ? " is-open" : ""}`}>
          {expanded && !isAskUser ? (
          <div className="tool-card-collapse-inner">
            {(chatList || listedChat) && onOpenChat && !isRunning ? (
              <div className="tool-execution-card-chat-list-wrap">
                {chatList ? (
                  <ChatListPanel data={chatList} allChats={allChats} onOpenChat={onOpenChat} />
                ) : listedChat ? (
                  <SingleChatPanel chat={listedChat} allChats={allChats} onOpenChat={onOpenChat} />
                ) : null}
              </div>
            ) : null}

            <div className="tool-execution-card-body">
              {isRunning && !isChatTool && (
                <div className="tool-execution-card-waiting-row">
                  <div className="status-dot online tool-execution-card-waiting-dot" />
                  {externalAgent
                    ? "Running inside the coding agent…"
                    : "Waiting for UEFN listener… If this lasts >30s, restart UEFN (listener wedged)."}
                </div>
              )}

              {isCancelled && (
                <div className="tool-execution-card-hint">
                  Canceled — you stopped the run before this tool finished.
                </div>
              )}

              {isGuardBlocked ? (
                <div className="tool-execution-card-hint">
                  Skipped — this exact call already ran this turn, so it was not repeated. The result is unchanged.
                </div>
              ) : !hideArgsForFileEdit && !isCancelled ? (
                <Body
                  toolName={meta.name}
                  args={meta.arguments}
                  argsText={argsText}
                  resultText={resultText}
                  isSuccess={isSuccess}
                  isError={!isSuccess && !isRunning}
                  showResult={!isRunning && !hideRawResult}
                  hideArgs={false}
                  hint={!isRunning ? meta.hint : undefined}
                  onOpenFile={onOpenFile}
                />
              ) : null}
            </div>
          </div>
          ) : null}
        </div>
      </div>
    </div>
  );
});
