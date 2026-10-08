import { useEffect, useRef, useState } from "react";

import { requestFocusGraph } from "../hooks/graphActivity";
import {
  dismissWorkflowRun,
  stopWorkflowRun,
  useChatWorkflowRuns,
  type ChatWorkflowRun,
  type ChatWorkflowStep,
} from "../hooks/workflowRunsByChat";
import { requestOpenWorkflowsTab } from "../navigation/openWorkflowsTab";

/** Yes/no gates are instant; the card lists them only when one fails. */
function shownSteps(run: ChatWorkflowRun): ChatWorkflowStep[] {
  return run.steps.filter((s) => s.type !== "logic.if" || s.state === "error");
}

/** The step running now: the latest one started (a Repeat stays running around its steps). */
function currentStep(run: ChatWorkflowRun): ChatWorkflowStep | undefined {
  return run.steps
    .filter((s) => s.state === "running")
    .sort((a, b) => (b.startedAt ?? 0) - (a.startedAt ?? 0))[0];
}

export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m >= 60
    ? `${Math.floor(m / 60)}h ${m % 60}m`
    : `${m}:${String(s).padStart(2, "0")}`;
}

export function workflowRunSummary(
  run: ChatWorkflowRun,
  now: number = Date.now(),
): string {
  const steps = shownSteps(run);
  const main = steps.filter((s) => !s.extra);
  if (run.state === "done")
    return `Finished · ${formatElapsed((run.endedAt ?? now) - run.startedAt)}`;
  if (run.state === "stopped") return "Stopped";
  if (run.state === "error") {
    const failed = steps.find((s) => s.state === "error");
    return failed ? `Failed at ${failed.label}` : "Failed";
  }
  const current = currentStep(run);
  if (!current) return "Starting…";
  const at = main.findIndex((s) => s.node === current.node);
  const took = current.startedAt
    ? ` · ${formatElapsed(now - current.startedAt)}`
    : "";
  return at >= 0
    ? `Step ${at + 1}/${main.length} · ${current.label}${took}`
    : `${current.label}${took}`;
}

/** Re-render once a second while the run is live, for the elapsed times. */
function useTick(live: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [live]);
  return now;
}

function StateMark({ state }: { state: ChatWorkflowStep["state"] }) {
  if (state === "ok") {
    return (
      <svg
        width="11"
        height="11"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        aria-hidden
      >
        <path d="M5 12.5l4.5 4.5L19 7.5" />
      </svg>
    );
  }
  if (state === "error") {
    return (
      <svg
        width="10"
        height="10"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        aria-hidden
      >
        <path d="M6 6l12 12M18 6L6 18" />
      </svg>
    );
  }
  return <span className="chat-workflow-run-dot" aria-hidden />;
}

/** The workflow this chat's ducky is running: name, every step, the one running now. */
export function ChatWorkflowRunCard({ chatId }: { chatId: string }) {
  const runs = useChatWorkflowRuns(chatId);
  return <>{runs.map((run) => <WorkflowRunCard key={run.run} chatId={chatId} run={run} />)}</>;
}

function WorkflowRunCard({ chatId, run }: { chatId: string; run: ChatWorkflowRun }) {
  const [open, setOpen] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState("");
  const stop = async () => {
    setStopping(true);
    setStopError("");
    try {
      await stopWorkflowRun(run.workflowId, run.run);
    } catch (error) {
      setStopError(error instanceof Error ? error.message : "Could not stop the workflow");
    } finally {
      setStopping(false);
    }
  };
  const running = run?.state === "running";
  const now = useTick(running);
  const currentRef = useRef<HTMLLIElement | null>(null);
  const current = run ? currentStep(run) : undefined;

  useEffect(() => {
    if (open) currentRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [open, current?.node]);

  if (!run) return null;
  const steps = shownSteps(run);
  const main = steps.filter((s) => !s.extra);
  const done = main.filter((s) => s.state === "ok").length;
  const percent =
    run.state === "done"
      ? 100
      : main.length
        ? Math.round((done / main.length) * 100)
        : 0;
  const openInEditor = () => {
    requestOpenWorkflowsTab();
    requestFocusGraph(run.workflowId, current ? { nodes: [current.node] } : {});
  };

  return (
    <div className="chat-pane-workflow-dock">
      <div
        className={`chat-plan-popup chat-workflow-run is-${run.state}`}
        data-testid="chat-workflow-run"
      >
        <div className="chat-plan-popup-bar chat-workflow-run-bar">
          <button
            type="button"
            className="chat-plan-popup-bar-main"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            title={open ? "Hide steps" : "Show steps"}
          >
            <span
              className={`chat-workflow-run-live is-${run.state}`}
              aria-hidden
            >
              {run.state === "running" ? (
                <span className="chat-workflow-run-pulse" />
              ) : (
                <StateMark
                  state={
                    run.state === "done"
                      ? "ok"
                      : run.state === "stopped"
                        ? "stopped"
                        : "error"
                  }
                />
              )}
            </span>
            <span className="chat-plan-popup-bar-kicker chat-workflow-run-kicker">
              Workflow
            </span>
            <span className="chat-plan-popup-bar-title">{run.name}</span>
            <span className="chat-plan-popup-bar-count chat-workflow-run-status">
              {workflowRunSummary(run, now)}
            </span>
            <span
              className={`chat-plan-popup-chevron${open ? " is-open" : ""}`}
              aria-hidden
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M6 9l6 6 6-6" />
              </svg>
            </span>
          </button>
          <button
            type="button"
            className="chat-plan-popup-bar-open"
            onClick={openInEditor}
            title="Open in Workflows"
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <path d="M15 3h6v6" />
              <path d="M10 14L21 3" />
            </svg>
          </button>
          {running ? (
            <button
              type="button"
              className="chat-plan-popup-bar-stop"
              onClick={() => void stop()}
              disabled={stopping}
              title="Stop the workflow"
              aria-label="Stop the workflow"
            >
              <svg
                width="12"
                height="12"
                viewBox="0 0 24 24"
                fill="currentColor"
                stroke="none"
              >
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              type="button"
              className="chat-plan-popup-bar-stop"
              onClick={() => {
                setStopError("");
                void dismissWorkflowRun(chatId, run.run).catch((error) =>
                  setStopError(error instanceof Error ? error.message : "Could not hide the workflow card"));
              }}
              title="Hide"
              aria-label="Hide workflow card"
            >
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M18 6L6 18" />
                <path d="M6 6l12 12" />
              </svg>
            </button>
          )}
          <span className="chat-workflow-run-progress" aria-hidden>
            <span style={{ width: `${percent}%` }} />
          </span>
        </div>
        {stopError ? <div role="alert" className="chat-workflow-run-error">{stopError}</div> : null}
        {open ? (
          <ol className="chat-workflow-run-steps">
            {steps.map((s) => (
              <li
                key={s.node}
                ref={s.node === current?.node ? currentRef : undefined}
                className={`chat-workflow-run-step is-${s.state}${s.extra ? " is-extra" : ""}${s.node === current?.node ? " is-current" : ""}`}
              >
                <span className="chat-workflow-run-mark">
                  <StateMark state={s.state} />
                </span>
                <span className="chat-workflow-run-label">{s.label}</span>
                {s.startedAt && (s.state === "running" || s.endedAt) ? (
                  <span className="chat-workflow-run-time">
                    {formatElapsed(
                      (s.state === "running" ? now : (s.endedAt ?? now)) -
                        s.startedAt,
                    )}
                  </span>
                ) : null}
                {s.error ? (
                  <span className="chat-workflow-run-error">{s.error}</span>
                ) : null}
              </li>
            ))}
          </ol>
        ) : null}
      </div>
    </div>
  );
}
