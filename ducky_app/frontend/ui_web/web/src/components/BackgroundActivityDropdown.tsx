import { subscribeAgentBackgroundActivity, openAgentBackgroundJob, stopAgentBackgroundJob } from "../hooks/agentBackgroundActivity";
import { formatElapsedMs } from "../hooks/chatTurnTimer";
import { useEffect, useRef, useState } from "react";
import { Icons } from "../icons/Icons";
import {
  clearFinishedBackgroundJobs,
  countWorkingBackgroundJobs,
  dismissBackgroundJob,
  requestBackgroundJobCancel,
  useBackgroundActivity,
  type BackgroundJob,
} from "../hooks/backgroundActivity";
import {
  applyBackgroundJobPush,
  requestFocusGraph,
  syncReadyGraphJobs,
  workflowIdFromJobId,
} from "../hooks/graphActivity";
import { subscribePanelPush } from "../hooks/usePanelPushBus";
import { refreshWorkflowRuns, stopWorkflowRun, subscribeWorkflowEvents } from "../hooks/workflowRunsByChat";
import { requestOpenWorkflowsTab } from "../navigation/openWorkflowsTab";
import { useWaitingItems } from "../hooks/waitingChats";
import { DropdownPanel } from "./DropdownPanel";
import { WaitingForYouSection } from "./WaitingForYouSection";

function phaseClass(phase: string): string {
  if (phase === "done" || phase === "ready") return "is-ok";
  if (phase === "error") return "is-off";
  return "is-warn";
}

function openGraphJob(job: BackgroundJob): void {
  const wid = workflowIdFromJobId(job.id);
  if (!wid) return;
  requestOpenWorkflowsTab();
  requestFocusGraph(wid);
}

export function BackgroundActivityDropdown() {
  const { jobs } = useBackgroundActivity();
  const jobsRef = useRef(jobs);
  jobsRef.current = jobs;
  const [now, setNow] = useState(Date.now);
  useEffect(() => subscribeAgentBackgroundActivity(), []);
  const openJob = (job: BackgroundJob) => {
    setOpen(false);
    if (job.source === "agent") void openAgentBackgroundJob(job).catch((error) => setActionError(String(error)));
    else openGraphJob(job);
  };
  const [open, setOpen] = useState(false);
  const [actionError, setActionError] = useState("");
  const cancel = async (job: BackgroundJob) => {
    setActionError("");
    if (job.source === "agent") {
      try {
        await stopAgentBackgroundJob(job);
      } catch (error) {
        setActionError(error instanceof Error ? error.message : "Could not stop that chat");
      }
      return;
    }
    const wid = workflowIdFromJobId(job.id);
    if (!wid) { requestBackgroundJobCancel(job.id); return; }
    try {
      const run = job.id.startsWith("graph-run:") ? job.id.slice(job.id.lastIndexOf(":") + 1) : "";
      await stopWorkflowRun(wid, run);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not stop the workflow");
    }
  };
  const anchorRef = useRef<HTMLButtonElement>(null);
  const working = countWorkingBackgroundJobs(jobs);
  const nowCount = working;
  const live = jobs.filter((j) => j.phase === "working");
  const past = jobs.filter((j) => j.phase !== "working" && j.phase !== "ready");

  // The elapsed times only show in the open tray's running rows. The header mounts this
  // in every window, so a clock running all the time woke every window once a second.
  const ticking = open && live.length > 0;
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [ticking]);

  useEffect(() => {
    const wipeIdle = () => syncReadyGraphJobs([], jobsRef.current);
    wipeIdle();
    // Keep snapshot polling alive even when no chat or workflow editor is mounted.
    const stopRuns = subscribeWorkflowEvents(() => {});
    void refreshWorkflowRuns();
    const stopPush = subscribePanelPush((event) => {
      if (event.type === "background_job") applyBackgroundJobPush(event);
      if (event.type === "graphs_changed") { wipeIdle(); void refreshWorkflowRuns(); }
    });
    return () => { stopRuns(); stopPush(); };
  }, []);

  useEffect(() => {
    if (open) { syncReadyGraphJobs([], jobsRef.current); void refreshWorkflowRuns(); }
  }, [open]);

  // Chats (or agents with no chat) blocked on a question or command card.
  const waiting = useWaitingItems().length;
  const title = [
    waiting ? `${waiting} waiting for your answer` : "",
    working ? `${working} running in the background` : "",
  ].filter(Boolean).join(" · ") || "Background activity";

  return (
    <div className="connection-status-root no-drag">
      <button
        ref={anchorRef}
        type="button"
        className={`no-drag connection-status-btn bg-activity-trigger${open ? " is-active" : ""}${nowCount ? " has-store-update" : ""}`}
        title={title}
        aria-label="Background activity"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
      >
        <Icons.Inbox />
        {waiting ? (
          <span className="bg-activity-waiting-count">
            <span className="chat-waiting-marker chat-waiting-marker--chip" aria-hidden="true">?</span>
            {waiting} waiting
          </span>
        ) : null}
        {nowCount ? <span className="bg-activity-running-count">{nowCount} running</span> : null}
      </button>
      <DropdownPanel
        anchorRef={anchorRef}
        open={open}
        onClose={() => setOpen(false)}
        width={360}
        placement="bottom"
      >
        <div className="connection-status-menu bg-activity-menu" role="dialog" aria-label="Background activity">
          {actionError ? <p role="alert">{actionError}</p> : null}
          <WaitingForYouSection onOpenChat={() => setOpen(false)} />
          <div className="connection-status-menu-head">Now</div>
          {live.length === 0 ? (
            <p className="bg-activity-empty">Nothing running. You can keep working.</p>
          ) : (
            live.map((job) => (
              <div
                key={job.id}
                className={`connection-status-menu-row ${phaseClass(job.phase)}`}
                role={(!!job.convId || !!workflowIdFromJobId(job.id)) ? "button" : undefined}
                tabIndex={job.convId || workflowIdFromJobId(job.id) ? 0 : undefined}
                onKeyDown={(event) => { if ((event.key === "Enter" || event.key === " ") && event.target === event.currentTarget) { event.preventDefault(); openJob(job); } }}
                onClick={(!!job.convId || !!workflowIdFromJobId(job.id)) ? () => openJob(job) : undefined}
              >
                <span className="connection-status-menu-dot" aria-hidden />
                <div className="connection-status-menu-text">
                  <span className="connection-status-menu-label">{job.title}</span>
                  <span className="connection-status-menu-detail">
                    {job.percent != null ? `${Math.round(job.percent)}% · ` : ""}
                    {job.detail || job.source}{job.startedAt ? ` \u00b7 ${formatElapsedMs(Math.max(0, now - job.startedAt))}` : ""}
                  </span>
                  {job.percent != null ? (
                    <progress
                      className="bg-activity-progress"
                      max={100}
                      value={Math.max(0, Math.min(100, job.percent))}
                    />
                  ) : null}
                  {job.cancelable || (job.source === "agent" && job.convId) ? (
                    <button
                      type="button"
                      className="bg-activity-link"
                      title={job.source === "agent" ? (job.toolId ? "Stops this chat's turn, and this command with it" : "Stops this chat's turn") : undefined}
                      onClick={(event) => { event.stopPropagation(); void cancel(job); }}
                    >
                      {job.source === "agent" || workflowIdFromJobId(job.id) ? "Stop" : "Cancel"}
                    </button>
                  ) : null}
                </div>
                {job.cancelable || (job.source === "agent" && job.convId) ? null : (
                  <button
                    type="button"
                    className="bg-activity-dismiss"
                    title="Hide from this list. It cannot be stopped from here and may still be running."
                    aria-label="Hide"
                    onClick={(event) => { event.stopPropagation(); dismissBackgroundJob(job.id); }}
                  >
                    ×
                  </button>
                )}
              </div>
            ))
          )}
          <div className="connection-status-menu-head bg-activity-head-row">
            <span>Earlier</span>
            {past.length ? (
              <button type="button" className="bg-activity-link" onClick={() => clearFinishedBackgroundJobs()}>
                Clear finished
              </button>
            ) : null}
          </div>
          {past.length === 0 ? (
            <p className="bg-activity-empty">No recent activity.</p>
          ) : (
            past.map((job) => (
              <div key={job.id} className={`connection-status-menu-row ${phaseClass(job.phase)}`}
                role={job.convId ? "button" : undefined} tabIndex={job.convId ? 0 : undefined}
                onClick={job.convId ? () => openJob(job) : undefined}
                onKeyDown={(event) => { if (job.convId && event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); openJob(job); } }}>
                <span className="connection-status-menu-dot" aria-hidden />
                <div className="connection-status-menu-text">
                  <span className="connection-status-menu-label">{job.title}</span>
                  <span className="connection-status-menu-detail">{job.detail || job.phase}</span>
                </div>
                <button
                  type="button"
                  className="bg-activity-dismiss"
                  title="Dismiss"
                  aria-label="Dismiss"
                  onClick={(event) => { event.stopPropagation(); dismissBackgroundJob(job.id); }}
                >
                  ×
                </button>
              </div>
            ))
          )}
        </div>
      </DropdownPanel>
    </div>
  );
}
