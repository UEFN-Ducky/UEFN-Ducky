import { useEffect, useState } from "react";
import { Icons } from "../icons/Icons";
import { TerminalOutput, type TerminalSnapshot } from "./TerminalOutput";

/** One node in the run going on now (or that just ended). */
export type LiveNodeRun = {
  state: "running" | "ok" | "error" | "stopped";
  started?: number;
  ended?: number;
  error?: string;
  output?: TerminalSnapshot;
  /** A Custom code step's ducky.log lines. */
  log?: string;
};

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m ? `${m}m ${String(s).padStart(2, "0")}s` : `${s}s`;
}

/** Details panel: what this step is doing right now, how long it took, why it failed, its log. */
export function LiveNodeStatus({ run }: { run: LiveNodeRun }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (run.state !== "running") return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [run.state]);
  const seconds = run.started ? Math.max(0, Math.round(((run.ended ?? now) - run.started) / 1000)) : 0;
  const status = run.state === "running" ? `Running · ${clock(seconds)}`
    : run.state === "ok" ? `Finished in ${clock(seconds)}`
    : run.state === "error" ? `Failed after ${clock(seconds)}`
    : `Stopped after ${clock(seconds)}`;
  return <section className={`aw-node-live is-${run.state}`} aria-label="This run">
    <strong>This run</strong>
    <p className="aw-node-live-state" aria-live="polite">{run.state === "running" ? <Icons.Spinner /> : null}{status}</p>
    {run.error ? <p className="aw-node-live-error">{run.error}</p> : null}
    {run.output ? <TerminalOutput snapshot={run.output} /> : null}
    {run.log ? <TerminalOutput snapshot={{ output: run.log, sessionId: "" }} title="Log" /> : null}
  </section>;
}
