import type { AutomationSummaryDto, PanelPushEvent } from "../types/panel";
import {
  dismissBackgroundJob,
  upsertBackgroundJob,
  type BackgroundJob,
} from "./backgroundActivity";

export const GRAPH_JOB_PREFIX = "graph:";

export function graphJobId(workflowId: string): string {
  return `${GRAPH_JOB_PREFIX}${workflowId}`;
}

export function applyBackgroundJobPush(event: PanelPushEvent | (Partial<BackgroundJob> & { id?: string })): void {
  const id = String(event.id || "").trim();
  if (!id) return;
  upsertBackgroundJob({
    id,
    source: event.source,
    title: "title" in event ? event.title : undefined,
    detail: event.detail,
    percent: "percent" in event ? event.percent : undefined,
    phase: event.phase,
    cancelable: "cancelable" in event ? event.cancelable : undefined,
  });
}

export function syncReadyGraphJobs(
  rows: Array<Pick<AutomationSummaryDto, "id" | "name" | "kind" | "enabled" | "node_count">>,
  current: BackgroundJob[],
): void {
  const wanted = new Set<string>();
  for (const row of rows) {
    const wid = String(row.id || "").trim();
    if (!wid || !row.enabled || !(row.node_count || 0)) continue;
    const id = graphJobId(wid);
    wanted.add(id);
    const prev = current.find((j) => j.id === id);
    if (prev?.phase === "working") continue;
    upsertBackgroundJob({
      id,
      source: row.kind === "pipeline" ? "pipeline" : "automation",
      title: row.name || wid,
      detail: "Ready to run",
      phase: "ready",
    });
  }
  for (const job of current) {
    if (!job.id.startsWith(GRAPH_JOB_PREFIX) || job.phase === "working") continue;
    if (job.phase === "ready" && !wanted.has(job.id)) dismissBackgroundJob(job.id);
  }
}
