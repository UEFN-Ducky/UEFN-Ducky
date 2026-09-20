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
import { applyBackgroundJobPush, GRAPH_JOB_PREFIX, syncReadyGraphJobs } from "../hooks/graphActivity";
import { getApi } from "../hooks/usePanelApi";
import { subscribePanelPush } from "../hooks/usePanelPushBus";
import { requestOpenAutomationsTab } from "../navigation/openAutomationsTab";
import { requestOpenPipelinesTab } from "../navigation/openPipelinesTab";
import { DropdownPanel } from "./DropdownPanel";

function phaseClass(phase: string): string {
  if (phase === "done" || phase === "ready") return "is-ok";
  if (phase === "error") return "is-off";
  return "is-warn";
}

function openGraphJob(job: BackgroundJob): void {
  if (!job.id.startsWith(GRAPH_JOB_PREFIX) && !job.id.startsWith("graph-run:")) return;
  if (job.source === "pipeline") requestOpenPipelinesTab();
  else requestOpenAutomationsTab();
}

export function BackgroundActivityDropdown() {
  const { jobs } = useBackgroundActivity();
  const jobsRef = useRef(jobs);
  jobsRef.current = jobs;
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const working = countWorkingBackgroundJobs(jobs);
  const ready = jobs.filter((j) => j.phase === "ready").length;
  const nowCount = working + ready;
  const live = jobs.filter((j) => j.phase === "working" || j.phase === "ready");
  const past = jobs.filter((j) => j.phase !== "working" && j.phase !== "ready");

  useEffect(() => {
    const load = () => {
      const api = getApi();
      if (!api) return;
      void Promise.all([api.list_automations?.(), api.list_pipelines?.()]).then(([autos, pipes]) => {
        syncReadyGraphJobs(
          [...(autos?.automations || []), ...(pipes?.pipelines || [])],
          jobsRef.current,
        );
      });
    };
    load();
    return subscribePanelPush((event) => {
      if (event.type === "background_job") applyBackgroundJobPush(event);
      if (event.type === "graphs_changed") load();
    });
  }, []);

  useEffect(() => {
    if (open) {
      const api = getApi();
      if (!api) return;
      void Promise.all([api.list_automations?.(), api.list_pipelines?.()]).then(([autos, pipes]) => {
        syncReadyGraphJobs(
          [...(autos?.automations || []), ...(pipes?.pipelines || [])],
          jobsRef.current,
        );
      });
    }
  }, [open]);

  const title = working
    ? `${working} running in the background`
    : ready
      ? `${ready} ready to run`
      : "Background activity";

  return (
    <div className="connection-status-root no-drag">
      <button
        ref={anchorRef}
        type="button"
        className={`no-drag connection-status-btn${open ? " is-active" : ""}${nowCount ? " has-store-update" : ""}`}
        title={title}
        aria-label="Background activity"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
      >
        <Icons.Inbox />
        {nowCount ? <span className="store-job-badge bg-activity-badge">{nowCount > 8 ? "8+" : nowCount}</span> : null}
      </button>
      <DropdownPanel
        anchorRef={anchorRef}
        open={open}
        onClose={() => setOpen(false)}
        width={360}
        placement="bottom"
      >
        <div className="connection-status-menu bg-activity-menu" role="dialog" aria-label="Background activity">
          <div className="connection-status-menu-head">Now</div>
          {live.length === 0 ? (
            <p className="bg-activity-empty">Nothing running. You can keep working.</p>
          ) : (
            live.map((job) => (
              <div
                key={job.id}
                className={`connection-status-menu-row ${phaseClass(job.phase)}`}
                role={job.id.startsWith(GRAPH_JOB_PREFIX) ? "button" : undefined}
                onClick={job.id.startsWith(GRAPH_JOB_PREFIX) ? () => openGraphJob(job) : undefined}
              >
                <span className="connection-status-menu-dot" aria-hidden />
                <div className="connection-status-menu-text">
                  <span className="connection-status-menu-label">{job.title}</span>
                  <span className="connection-status-menu-detail">
                    {job.percent != null ? `${Math.round(job.percent)}% · ` : ""}
                    {job.detail || job.source}
                  </span>
                  {job.percent != null ? (
                    <progress
                      className="bg-activity-progress"
                      max={100}
                      value={Math.max(0, Math.min(100, job.percent))}
                    />
                  ) : null}
                  {job.cancelable ? (
                    <button
                      type="button"
                      className="bg-activity-link"
                      onClick={() => requestBackgroundJobCancel(job.id)}
                    >
                      Cancel
                    </button>
                  ) : null}
                </div>
              </div>
            ))
          )}
          <div className="connection-status-menu-head bg-activity-head-row">
            <span>Earlier</span>
            {past.length ? (
              <button type="button" className="bg-activity-link" onClick={() => clearFinishedBackgroundJobs()}>
                Clear
              </button>
            ) : null}
          </div>
          {past.length === 0 ? (
            <p className="bg-activity-empty">No recent downloads or installs.</p>
          ) : (
            past.map((job) => (
              <div key={job.id} className={`connection-status-menu-row ${phaseClass(job.phase)}`}>
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
                  onClick={() => dismissBackgroundJob(job.id)}
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
