import { useEffect, useState } from "react";
import type { PlanNode } from "../types/panel";
import { useBackgroundActivity, type BackgroundJob } from "../hooks/backgroundActivity";
import { formatMinutes } from "../hooks/chatTurnTimer";
import "./WorkProgressBar.css";

export type WorkProgress = { done: number; total: number };

/** One thing the open chat waits on: its own turn or another agent's. */
export type WorkRow = { convId: string; name: string; progress: WorkProgress | null };

const MAX_ROWS = 3;
const TICK_MS = 30_000;
/** The run's own bar never shows done before the run says so. */
const RUN_BAR_CAP = 0.95;

function countLeaves(node: PlanNode): WorkProgress {
  if (!node.children?.length) {
    const done = node.status === "completed" || node.status === "cancelled" ? 1 : 0;
    return { done, total: 1 };
  }
  return node.children.reduce(
    (sum, child) => {
      const c = countLeaves(child);
      return { done: sum.done + c.done, total: sum.total + c.total };
    },
    { done: 0, total: 0 },
  );
}

/** Done/total steps of the plan section assigned to `convId`, or null when it owns none. */
export function subPlanProgress(nodes: PlanNode[] | undefined, convId: string): WorkProgress | null {
  for (const node of nodes ?? []) {
    if (node.assignee === convId) return countLeaves(node);
    const nested = subPlanProgress(node.children, convId);
    if (nested) return nested;
  }
  return null;
}

type RunEta = { elapsed: number; expected: number; label: string; late: boolean };

/** Time left on the job's waited command, from its expected duration; null when unknown. */
function runEta(job: BackgroundJob | undefined, now: number): RunEta | null {
  const expected = job?.expectedMs || 0;
  if (!job || expected <= 0) return null;
  const elapsed = Math.max(0, now - (job.expectStartedAt || job.startedAt || now));
  if (elapsed > expected) return { elapsed, expected, label: "taking longer than expected", late: true };
  const left = expected - elapsed;
  const time = left < 60_000 ? "<1 min" : formatMinutes(left);
  return { elapsed, expected, label: `${job.estimated ? "about " : "~"}${time} left`, late: false };
}

/** Rows above the composer, one line each: what the waited-on chat does, the time left when
 *  its run has an estimate, how far along (its name is the tooltip). Never the time spent:
 *  "49 min" read like an estimate and said nothing about when it ends. */
export function WorkProgressBar({ rows, onOpen }: { rows: WorkRow[]; onOpen: (convId: string) => void }) {
  const { jobs } = useBackgroundActivity();
  const [now, setNow] = useState(Date.now);
  const shown = rows.length > 0;
  useEffect(() => {
    if (!shown) return;
    setNow(Date.now());
    const t = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(t);
  }, [shown]);
  if (!shown) return null;

  const extra = rows.length - MAX_ROWS;
  return (
    <div className="work-progress" role="status">
      {rows.slice(0, MAX_ROWS).map((row) => {
        const job = jobs.find((j) => j.phase === "working" && j.convId === row.convId);
        const eta = runEta(job, now);
        const meta = [job?.detail, eta?.label].filter(Boolean).join(" · ");
        const p = row.progress;
        return (
          <button key={row.convId} type="button" className="work-progress__row" title={row.name} onClick={() => onOpen(row.convId)}>
            {meta ? <span className={`work-progress__text${eta?.late ? " work-progress__late" : ""}`}>{meta}</span> : null}
            <span className="work-progress__bars">
              {/* No value = the native indeterminate bar. */}
              <progress className="work-progress__bar" max={p?.total || undefined} value={p ? p.done : undefined} />
              {eta ? (
                <progress
                  className="work-progress__bar work-progress__bar--run"
                  max={eta.expected}
                  value={Math.min(eta.elapsed, eta.expected * RUN_BAR_CAP)}
                />
              ) : null}
            </span>
            {p ? <span className="work-progress__count">{p.done}/{p.total}</span> : null}
          </button>
        );
      })}
      {extra > 0 ? <div className="work-progress__more">+{extra} more</div> : null}
    </div>
  );
}
