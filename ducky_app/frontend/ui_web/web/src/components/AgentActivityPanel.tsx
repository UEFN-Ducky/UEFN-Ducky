import { useRef, useState, useEffect } from "react";
import { createPortal } from "react-dom";
import { Icons } from "../icons/Icons";
import { DuckyAvatar } from "./ducky/DuckyAvatars";
import {
  activityPanelTitle,
  type ActivityLine,
} from "../utils/agentActivity";
import { ElapsedTimer } from "./ElapsedTimer";

const DRAG_THRESHOLD_PX = 4;
const COLLAPSED_MAX_LINES = 5;
const DUCKY_HOVER_PREVIEW_SIZE = 300;
const DUCKY_HOVER_PREVIEW_GAP = 12;

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

export function AgentActivityPanel({
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

  const liveTitle = activityPanelTitle(lines, isWaitingOnLinked, waitingCount, waitingTitle, statusText);
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
          <span className="agent-activity-panel-title">
            {title}
            {showThinkingDots ? (
              <span className="agent-activity-panel-thinking-dots" aria-hidden="true" />
            ) : null}
          </span>
          {chatId ? (
            <ElapsedTimer
              chatId={chatId}
              when={showIdleTimer ? "idle" : "live"}
              idlePrefix="Took"
              className="elapsed-timer--footer"
            />
          ) : null}
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
}
