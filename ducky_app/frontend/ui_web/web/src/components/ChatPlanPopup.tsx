import { memo, useEffect, useMemo, useState, type MouseEvent, type ReactNode } from "react";
import type { ChatPlan, PlanProgress } from "../types/panel";
import type { OpenFileHandler } from "../types/richContent";
import { useDockReveal } from "../hooks/useDockReveal";
import { requestOpenPlanTab } from "../navigation/openPlanTab";
import { PlanTodoCard, countProgress } from "./PlanTodoCard";
import { PlanKeeperPill } from "./PlanKeeperPill";
import { RichContentRenderer } from "./rich-content/RichContentRenderer";

interface ChatPlanPopupProps {
  plan: ChatPlan;
  progress?: PlanProgress | null;
  onOpenPlan?: () => void;
  onStopTracking?: () => void | Promise<void>;
  onOpenFile?: OpenFileHandler;
  /** Shown at the top of the opened panel, above the steps (a member's Lead / Full plan links). */
  header?: ReactNode;
  /** Lets the sticky query wrapper raise its z-index without a :has() selector. */
  onOpenChange?: (open: boolean) => void;
}

/** Collapsible plan pill. Active: docked above the composer. Finished: under its turn in history. */
export const ChatPlanPopup = memo(function ChatPlanPopup({
  plan,
  progress,
  onOpenPlan,
  onStopTracking,
  onOpenFile,
  header,
  onOpenChange,
}: ChatPlanPopupProps) {
  const [open, setOpen] = useState(false);
  // One card growing upward from the dock, body mounted through the close animation.
  const { popupRef, present, expanded } = useDockReveal(open, ".chat-pane-plan-dock");
  useEffect(() => {
    onOpenChange?.(open);
  }, [open, onOpenChange]);
  const [stopping, setStopping] = useState(false);
  const { done, total } = useMemo(() => countProgress(plan, progress), [plan, progress]);
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
        {allDone ? null : <PlanKeeperPill plan={plan} />}
        {plan.team_plan ? (
          <button
            type="button"
            className="chat-plan-popup-bar-main-plan"
            onClick={() => requestOpenPlanTab({ chatId: plan.team_plan!.chat_id, title: plan.team_plan!.title })}
            title={`Part of the team plan “${plan.team_plan.title}”. Open it.`}
          >
            Main plan
          </button>
        ) : null}
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
              {header}
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
