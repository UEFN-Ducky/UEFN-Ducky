import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import type { ChatPlan, PlanProgress } from "../types/panel";
import type { OpenFileHandler } from "../types/richContent";
import { progressForPlan } from "../utils/planOutlineNav";
import { PlanTodoCard } from "./PlanTodoCard";
import { RichContentRenderer } from "./rich-content/RichContentRenderer";

interface ChatPlanPopupProps {
  plan: ChatPlan;
  progress?: PlanProgress | null;
  onOpenPlan?: () => void;
  onStopTracking?: () => void | Promise<void>;
  onOpenFile?: OpenFileHandler;
  /** Lets the sticky query wrapper raise its z-index without a :has() selector. */
  onOpenChange?: (open: boolean) => void;
}

function count(plan: ChatPlan, progress?: PlanProgress | null): { done: number; total: number } {
  const counts = progress ?? progressForPlan(plan);
  return { done: counts.completed, total: counts.total };
}

/** Collapsible plan pill. Active: docked above the composer. Finished: under its turn in history. */
export const ChatPlanPopup = memo(function ChatPlanPopup({
  plan,
  progress,
  onOpenPlan,
  onStopTracking,
  onOpenFile,
  onOpenChange,
}: ChatPlanPopupProps) {
  const [open, setOpen] = useState(false);
  // `present` keeps the body mounted through the close animation. `expanded` flips a frame later so height can transition.
  const [present, setPresent] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const popupRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    onOpenChange?.(open);
  }, [open, onOpenChange]);
  useLayoutEffect(() => {
    if (open) {
      setPresent(true);
      return;
    }
    setExpanded(false);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const timer = window.setTimeout(() => setPresent(false), reduce ? 0 : 260);
    return () => window.clearTimeout(timer);
  }, [open]);
  useLayoutEffect(() => {
    if (!open || !present) return;
    const frame = requestAnimationFrame(() => setExpanded(true));
    return () => cancelAnimationFrame(frame);
  }, [open, present]);
  // Keep the open card's top inside the chat pane so the bar stays on screen and the body can scroll.
  useLayoutEffect(() => {
    const popup = popupRef.current;
    if (!present || !popup) return;
    const dock = popup.closest(".chat-pane-plan-dock");
    const pane = popup.closest(".chat-pane-root");
    if (!(dock instanceof HTMLElement) || !(pane instanceof HTMLElement)) return;
    const input = popup.closest(".chat-pane-input-area");
    const fit = () => {
      const room = dock.getBoundingClientRect().bottom - pane.getBoundingClientRect().top - 8;
      popup.style.setProperty("--plan-open-max", `${Math.max(96, Math.floor(room))}px`);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(pane);
    if (input instanceof HTMLElement) ro.observe(input);
    return () => {
      ro.disconnect();
      popup.style.removeProperty("--plan-open-max");
    };
  }, [present]);
  const [stopping, setStopping] = useState(false);
  const { done, total } = useMemo(() => count(plan, progress), [plan, progress]);
  const allDone = total > 0 && done >= total;

  const handleStop = async (e: MouseEvent) => {
    e.stopPropagation();
    if (!onStopTracking || stopping) return;
    setStopping(true);
    try {
      await onStopTracking();
    } finally {
      setStopping(false);
    }
  };

  return (
    <div ref={popupRef} className={`chat-plan-popup${expanded ? " is-open" : ""}${present ? " is-present" : ""}`}>
      <div className="chat-plan-popup-bar">
        <button
          type="button"
          className="chat-plan-popup-bar-main"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          title={open ? "Collapse plan" : "Expand plan"}
        >
          <span className="chat-plan-popup-bar-kicker">Plan</span>
          <span className="chat-plan-popup-bar-title">{plan.title || "Plan"}</span>
          <span className="chat-plan-popup-bar-count">
            {allDone ? "Finished" : total ? `${done}/${total}` : "No steps"}
          </span>
          <span className={`chat-plan-popup-chevron${expanded ? " is-open" : ""}`} aria-hidden>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 9l6 6 6-6" />
            </svg>
          </span>
        </button>
        {onOpenPlan ? (
          <button
            type="button"
            className="chat-plan-popup-bar-open"
            onClick={onOpenPlan}
            title="Open plan in tab"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <path d="M15 3h6v6" />
              <path d="M10 14L21 3" />
            </svg>
          </button>
        ) : null}
        {onStopTracking ? (
          <button
            type="button"
            className="chat-plan-popup-bar-stop"
            onClick={(e) => void handleStop(e)}
            disabled={stopping}
            title="Stop tracking plan"
            aria-label="Stop tracking plan"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18" />
              <path d="M6 6l12 12" />
            </svg>
          </button>
        ) : null}
      </div>
      {present ? (
        <div className="chat-plan-popup-reveal">
          <div className="chat-plan-popup-clip">
            <div className="chat-plan-popup-panel">
              <PlanTodoCard plan={plan} progress={progress} embedded />
              {plan.body_markdown ? (
                <div className="chat-plan-popup-md">
                  <RichContentRenderer text={plan.body_markdown} onOpenFile={onOpenFile} />
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
});
