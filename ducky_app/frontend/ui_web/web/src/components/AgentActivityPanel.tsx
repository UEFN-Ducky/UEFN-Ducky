import { memo, useRef, useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { Icons } from "../icons/Icons";
import { DuckyAvatar } from "./ducky/DuckyAvatars";
import {
  activityPanelTitle,
  type ActivityLine,
} from "../utils/agentActivity";
import { ElapsedTimer } from "./ElapsedTimer";
import { formatElapsedMs } from "../hooks/chatTurnTimer";

const DRAG_THRESHOLD_PX = 4;
const COLLAPSED_MAX_LINES = 5;
const DUCKY_HOVER_PREVIEW_SIZE = 300;
const DUCKY_HOVER_PREVIEW_GAP = 12;
/** No new output for this long while writing or thinking reads as "working on the next step". */
const QUIET_AFTER_MS = 15_000;
const QUIET_HINT =
  "The model is still working. Codex and other coding agents send nothing while they reason, and on a big chat " +
  "(100k+ tokens of context) each step can take 30 seconds to a few minutes. A long plan, file or edit shows up " +
  "only once it is written out. Ducky restarts the step by itself if Codex stays silent for 15 minutes.";

interface AgentActivityPanelProps {
  lines: ActivityLine[];
  duckyStyle?: string;
  /** Render just the status header (tool cards/stream already shown inline in the chat). */
  headerOnly?: boolean;
  isWaitingOnLinked: boolean;
  waitingTitle?: string;
  waitingCount: number;
  autoExpand?: boolean;
  /** Backend status line when there are no tool/stream activity lines yet. */
  statusText?: string;
  /** Chat id for the shared turn timer (footer clock). */
  chatId?: string;
  /** When true, show the frozen "Took Xm" footer after the turn ends. */
  showIdleTimer?: boolean;
}

export const AgentActivityPanel = memo(function AgentActivityPanel({
  lines,
  duckyStyle,
  headerOnly = false,
  isWaitingOnLinked,
  waitingTitle,
  waitingCount,
  autoExpand = false,
  statusText = "",
  chatId = "",
  showIdleTimer = false,
}: AgentActivityPanelProps) {
  const [expanded, setExpanded] = useState(autoExpand);
  const [duckPreviewPos, setDuckPreviewPos] = useState<{ left: number; top: number } | null>(null);
  const pointerStart = useRef({ x: 0, y: 0, dragging: false });
  const duckRef = useRef<HTMLDivElement>(null);

  const showDuckPreview = () => {
    const el = duckRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const size = DUCKY_HOVER_PREVIEW_SIZE;
    const gap = DUCKY_HOVER_PREVIEW_GAP;
    let left = rect.left + rect.width / 2 - size / 2;
    let top = rect.top - size - gap;
    left = Math.max(8, Math.min(left, window.innerWidth - size - 8));
    if (top < 8) top = rect.bottom + gap;
    setDuckPreviewPos({ left, top });
  };

  const hideDuckPreview = () => setDuckPreviewPos(null);

  useEffect(() => {
    if (autoExpand) setExpanded(true);
  }, [autoExpand, lines.length]);

  // When the stream stops (the model is composing a big tool call it shows only when done),
  // "Writing…" would look stuck: say so, with how long it has been quiet.
  const last = lines[lines.length - 1];
  const signature = `${lines.length}:${last?.status ?? ""}:${last?.text.length ?? 0}:${statusText}`;
  const [changedAt, setChangedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { setChangedAt(Date.now()); }, [signature]);
  const live = !showIdleTimer && !isWaitingOnLinked;
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, [live]);
  const quietMs = live && !lines.some((line) => line.status === "pending") ? Math.max(0, now - changedAt) : 0;
  const quiet = quietMs >= QUIET_AFTER_MS;

  // Quiet: the model is reasoning about what to do next. Say so, and what it did last.
  const lastDone = [...lines].reverse().find((line) => line.status === "success" || line.status === "error");
  const liveTitle = quiet
    ? "Thinking about the next step"
    : activityPanelTitle(lines, isWaitingOnLinked, waitingCount, waitingTitle, statusText);
  const title = showIdleTimer ? "Done" : liveTitle;
  const showBody = !headerOnly && !showIdleTimer && lines.length > 0;
  const showThinkingDots =
    !showBody &&
    !showIdleTimer &&
    !isWaitingOnLinked &&
    (lines.some((line) => line.status === "thinking") ||
      lines.length === 0 ||
      title === "Thinking" ||
      title.startsWith("Starting ") ||
      title.startsWith("Resumed "));
  const visibleLines = expanded ? lines : lines.slice(-COLLAPSED_MAX_LINES);
  const hiddenCount = expanded ? 0 : Math.max(0, lines.length - COLLAPSED_MAX_LINES);

  const handleHeaderPointerDown = (event: React.PointerEvent) => {
    pointerStart.current = { x: event.clientX, y: event.clientY, dragging: false };
  };

  const handleHeaderPointerMove = (event: React.PointerEvent) => {
    const dx = event.clientX - pointerStart.current.x;
    const dy = event.clientY - pointerStart.current.y;
    if (Math.hypot(dx, dy) >= DRAG_THRESHOLD_PX) {
      pointerStart.current.dragging = true;
    }
  };

  const handleHeaderPointerUp = () => {
    if (!pointerStart.current.dragging) {
      setExpanded((value) => !value);
    }
    pointerStart.current.dragging = false;
  };

  return (
    <div className={`agent-activity-panel${showIdleTimer ? " agent-activity-panel--idle" : ""}`}>
      <div
        className={`agent-activity-panel-header${expanded ? " agent-activity-panel-header--expanded" : ""}`}
        role={showIdleTimer ? undefined : "button"}
        tabIndex={showIdleTimer ? undefined : 0}
        aria-expanded={showIdleTimer ? undefined : expanded}
        onPointerDown={showIdleTimer ? undefined : handleHeaderPointerDown}
        onPointerMove={showIdleTimer ? undefined : handleHeaderPointerMove}
        onPointerUp={showIdleTimer ? undefined : handleHeaderPointerUp}
        onKeyDown={
          showIdleTimer
            ? undefined
            : (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setExpanded((value) => !value);
                }
              }
        }
      >
        <div className="agent-activity-panel-header-main">
          <div
            ref={duckRef}
            className="agent-activity-panel-duck"
            onMouseEnter={showDuckPreview}
            onMouseLeave={hideDuckPreview}
            onPointerDown={(event) => event.stopPropagation()}
          >
            <DuckyAvatar
              styleId={duckyStyle}
              size={22}
              className="ducky-avatar--22"
            />
          </div>
          {/* Title, then the quiet note and timer; they wrap under a long title instead of
              squeezing it into a narrow column. */}
          <div className="agent-activity-panel-text">
            <span className="agent-activity-panel-title" title={title}>
              {title}
              {showThinkingDots || quiet ? (
                <span className="agent-activity-panel-thinking-dots" aria-hidden="true" />
              ) : null}
            </span>
            {quiet || (chatId && showIdleTimer) ? (
              <span className="agent-activity-panel-meta">
                {quiet ? (
                  <span className="agent-activity-panel-quiet" title={QUIET_HINT}>
                    {formatElapsedMs(quietMs)} quiet
                    {lastDone ? ` · last: ${lastDone.text}` : ""}
                  </span>
                ) : null}
                {/* Only "Took X" once done: a clock counting up while it works read like an
                    estimate of when it ends. */}
                {chatId && showIdleTimer ? (
                  <ElapsedTimer chatId={chatId} when="idle" idlePrefix="Took" className="elapsed-timer--footer" />
                ) : null}
              </span>
            ) : null}
          </div>
        </div>
        {showBody ? (
          <span
            className={`agent-activity-panel-chevron${expanded ? " agent-activity-panel-chevron--expanded" : ""}`}
            aria-hidden="true"
          >
            <Icons.ChevronDown />
          </span>
        ) : null}
      </div>

      {showBody ? (
        <div
          className={`selectable-text agent-activity-panel-body${expanded ? " agent-activity-panel-body--expanded" : ""}`}
        >
          {hiddenCount > 0 ? (
            <div className="agent-activity-panel-truncated-hint">
              {hiddenCount} earlier step{hiddenCount === 1 ? "" : "s"} hidden — expand to view
            </div>
          ) : null}
          {visibleLines.map((line) => (
            <div
              key={line.id}
              className={`agent-activity-panel-line agent-activity-panel-line--${line.status}`}
            >
              {line.status === "pending" ? (
                <span className="agent-activity-panel-line-dot" aria-hidden="true" />
              ) : null}
              <span className="agent-activity-panel-line-text">{line.text}</span>
            </div>
          ))}
        </div>
      ) : null}

      {isWaitingOnLinked ? (
        <div className="agent-activity-panel-waiting-hint">
          Parent chat pauses until linked chat finishes or you press Stop on its card.
        </div>
      ) : null}

      {duckPreviewPos
        ? createPortal(
            <div
              className="agent-activity-panel-duck-preview"
              style={{ left: duckPreviewPos.left, top: duckPreviewPos.top }}
              aria-hidden="true"
            >
              <DuckyAvatar styleId={duckyStyle} size={DUCKY_HOVER_PREVIEW_SIZE} />
            </div>,
            document.body,
          )
        : null}
    </div>
  );
});
