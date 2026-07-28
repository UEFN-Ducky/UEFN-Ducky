import { useMemo, useState, type MouseEvent } from "react";
import type { ChatPlan, PlanProgress } from "../types/panel";
import type { OpenFileHandler } from "../types/richContent";
import { PlanTodoCard } from "./PlanTodoCard";
import { MarkdownContent } from "./rich-content/MarkdownContent";

interface ChatPlanPopupProps {
  plan: ChatPlan;
  progress?: PlanProgress | null;
  onOpenPlan?: () => void;
  onStopTracking?: () => void | Promise<void>;
  onOpenFile?: OpenFileHandler;
}

function count(plan: ChatPlan, progress?: PlanProgress | null): { done: number; total: number } {
  if (progress) return { done: progress.completed, total: progress.total };
  const todos = plan.todos || [];
  return { done: todos.filter((t) => t.status === "completed").length, total: todos.length };
}

/** Collapsible plan pill. Active: docked above the composer. Finished: under its turn in history. */
export function ChatPlanPopup({
  plan,
  progress,
  onOpenPlan,
  onStopTracking,
  onOpenFile,
}: ChatPlanPopupProps) {
  const [open, setOpen] = useState(false);
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
    <div className={`chat-plan-popup${open ? " is-open" : ""}`}>
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
          <span className={`chat-plan-popup-chevron${open ? " is-open" : ""}`} aria-hidden>
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
      {open ? (
        <div className="chat-plan-popup-panel">
          <PlanTodoCard plan={plan} progress={progress} onOpenPlan={onOpenPlan} />
          {plan.body_markdown ? (
            <div className="chat-plan-popup-md">
              <MarkdownContent text={plan.body_markdown} onOpenFile={onOpenFile} />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
