import { Icons } from "../../../icons/Icons";
import {
  installProgressPct,
  type CardBusy,
} from "./storeData";

type Props = {
  jobs: CardBusy[];
  onOpen: (slug: string) => void;
  onDismiss: (slug: string) => void;
  onDismissAll: () => void;
};

/** Stacked install/update popups — one card per concurrent Store job. */
export function StoreJobStack({ jobs, onOpen, onDismiss, onDismissAll }: Props) {
  if (!jobs.length) return null;
  const working = jobs.filter((j) => j.phase === "working").length;
  const done = jobs.filter((j) => j.phase === "done").length;
  const errored = jobs.filter((j) => j.phase === "error").length;
  const summary =
    working > 0
      ? `${working} updating…`
      : errored > 0
        ? `${errored} failed`
        : `${done} done`;
  return (
    <div className="ds-job-stack" aria-live="polite">
      <div className="ds-job-stack-tray">
        <div className="ds-job-stack-header">
          <span className="ds-job-stack-title">
            Updates <span className="ds-job-stack-count">{summary}</span>
          </span>
          <button
            type="button"
            className="ds-job-stack-close-all"
            onClick={onDismissAll}
            title="Close all update toasts"
          >
            Close all
          </button>
        </div>
        <div className="ds-job-stack-list">
          {jobs.map((job) => {
            const pct = installProgressPct(job);
            const isWorking = job.phase === "working";
            const isError = job.phase === "error";
            const title = job.name || job.slug;
            return (
              <div
                key={job.slug}
                className={[
                  "ds-job-toast",
                  isWorking ? "ds-job-toast--working" : "",
                  job.phase === "done" ? "ds-job-toast--done" : "",
                  isError ? "ds-job-toast--error" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
              >
                <button
                  type="button"
                  className="ds-job-toast-main"
                  onClick={() => onOpen(job.slug)}
                  title="Open item"
                >
                  <span className="ds-job-toast-icon" aria-hidden>
                    {isError ? (
                      <Icons.ErrorCircle />
                    ) : job.phase === "done" ? (
                      <Icons.Check />
                    ) : (
                      <Icons.Download />
                    )}
                  </span>
                  <span className="ds-job-toast-text">
                    <span className="ds-job-toast-title">{title}</span>
                    <span className="ds-job-toast-label">{job.label}</span>
                  </span>
                </button>
                <button
                  type="button"
                  className="ds-job-toast-close"
                  aria-label={`Close ${title}`}
                  title="Close"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    onDismiss(job.slug);
                  }}
                >
                  <Icons.Close />
                  <span>Close</span>
                </button>
                <div className="ds-job-toast-progress">
                  <div
                    className="ds-progress-track"
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={pct}
                    aria-label={`${title} ${pct}%`}
                  >
                    <div
                      className={`ds-progress-fill${isWorking ? "" : " ds-progress-fill--done"}${
                        isError ? " ds-progress-fill--error" : ""
                      }`}
                      style={{ width: `${isError ? 100 : pct}%` }}
                    />
                  </div>
                  <span className="ds-progress-pct">{pct}%</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
