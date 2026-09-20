import type { AutomationSummaryDto, PanelPushEvent } from "../types/panel";
import {
  dismissBackgroundJob,
  upsertBackgroundJob,
  type BackgroundJob,
} from "./backgroundActivity";

export const GRAPH_JOB_PREFIX = "graph:";
export const GRAPH_RUN_PREFIX = "graph-run:";

export function graphJobId(workflowId: string): string {
  return `${GRAPH_JOB_PREFIX}${workflowId}`;
}

export function workflowIdFromJobId(jobId: string): string {
  const id = String(jobId || "");
  if (id.startsWith(GRAPH_RUN_PREFIX)) {
    const rest = id.slice(GRAPH_RUN_PREFIX.length);
    const cut = rest.lastIndexOf(":");
    return cut > 0 ? rest.slice(0, cut) : rest;
  }
  if (id.startsWith(GRAPH_JOB_PREFIX)) return id.slice(GRAPH_JOB_PREFIX.length);
  return "";
}

type GraphKind = "automation" | "pipeline";
let pendingFocus: { kind: GraphKind; id: string } | null = null;

export function requestFocusGraph(kind: GraphKind, id: string): void {
  const wid = id.trim();
  if (!wid) return;
  pendingFocus = { kind, id: wid };
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("ducky:focus-graph", { detail: pendingFocus }));
  }
}

export function takePendingGraphFocus(kind: GraphKind): string {
  if (pendingFocus?.kind !== kind) return "";
  const id = pendingFocus.id;
  pendingFocus = null;
  return id;
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
