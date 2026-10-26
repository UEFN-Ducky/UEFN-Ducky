import type { AutomationSummaryDto, PanelPushEvent } from "../types/panel";
import { holdGuidedUi } from "../ui-targets/guidedBusy";
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

/** Where to look once the workflow is open: nodes to bring into view (and select), a caption.
 *  ``quiet``: an agent's, so an editor on another workflow stays where it is. */
export type GraphFocusTarget = { nodes?: string[]; select?: boolean; note?: string; quiet?: boolean };
export type GraphFocus = GraphFocusTarget & { id: string };

let pendingFocus: GraphFocus | null = null;

export function requestFocusGraph(id: string, target: GraphFocusTarget = {}): void {
  const wid = id.trim();
  if (!wid) return;
  pendingFocus = { id: wid, ...target };
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("ducky:focus-graph", { detail: pendingFocus }));
  }
}

/** Chat saved, showed or deleted a workflow. None of them opens the Workflows tab or moves
 *  an open editor off the workflow the person is on: the chat card's Show me does that when
 *  they press it. A save refreshes that workflow if it is the open one; a show is kept, so
 *  opening Workflows lands on its nodes. */
export function applyGraphFocusPush(event: PanelPushEvent): void {
  if (event.type !== "graph_focus") return;
  const id = String(event.id || "").trim();
  if (!id) return;
  const show = event.action === "show";
  // Ducky is working in the editor: its first-open tour waits for another time.
  if (show) holdGuidedUi();
  if (event.action === "deleted") {
    if (pendingFocus?.id === id) pendingFocus = null;
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("ducky:graph-deleted", { detail: { id } }));
    }
    return;
  }
  const nodes = Array.isArray(event.nodes) ? event.nodes.map(String).filter(Boolean) : [];
  requestFocusGraph(id, { ...(nodes.length ? { nodes } : {}), ...(event.select ? { select: true } : {}), ...(event.note ? { note: String(event.note) } : {}), quiet: true });
  // A save is only for an editor already open: opening Workflows later shows what you left there.
  if (!show) pendingFocus = null;
}

export function takePendingGraphFocus(): string {
  return takePendingGraphFocusTarget()?.id || "";
}

export function takePendingGraphFocusTarget(): GraphFocus | null {
  const focus = pendingFocus;
  pendingFocus = null;
  return focus;
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
    convId: "conv_id" in event && event.conv_id ? event.conv_id : undefined,
  });
}

export function syncReadyGraphJobs(
  _rows: Array<Pick<AutomationSummaryDto, "id" | "name" | "enabled" | "node_count">>,
  current: BackgroundJob[],
): void {
  for (const job of current) {
    if (job.id.startsWith(GRAPH_JOB_PREFIX) && job.phase === "ready") {
      dismissBackgroundJob(job.id);
    }
  }
}
