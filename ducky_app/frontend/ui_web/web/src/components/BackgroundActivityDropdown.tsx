import { useRef, useState } from "react";
import { Icons } from "../icons/Icons";
import {
  clearFinishedBackgroundJobs,
  countWorkingBackgroundJobs,
  dismissBackgroundJob,
  requestBackgroundJobCancel,
  useBackgroundActivity,
} from "../hooks/backgroundActivity";
import { DropdownPanel } from "./DropdownPanel";

function phaseClass(phase: string): string {
  if (phase === "done") return "is-ok";
  if (phase === "error") return "is-off";
  return "is-warn";
}

export function BackgroundActivityDropdown() {
  const { jobs } = useBackgroundActivity();
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const working = countWorkingBackgroundJobs(jobs);
  const live = jobs.filter((j) => j.phase === "working");
  const past = jobs.filter((j) => j.phase !== "working");

  return (
    <div className="connection-status-root no-drag">
      <button
        ref={anchorRef}
        type="button"
        className={`no-drag connection-status-btn${open ? " is-active" : ""}${working ? " has-store-update" : ""}`}
        title={working ? `${working} running in the background` : "Background activity"}
        aria-label="Background activity"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
      >
        <Icons.Inbox />
        {working ? <span className="store-job-badge bg-activity-badge">{working > 8 ? "8+" : working}</span> : null}
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
              <div key={job.id} className={`connection-status-menu-row ${phaseClass(job.phase)}`}>
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
