import type { ChatPlan, PlanNode, PlanProgress } from "../types/panel";

function walkStarted(nodes: PlanNode[] | undefined): boolean {
  for (const n of nodes || []) {
    if (n.status === "in_progress" || n.status === "completed") return true;
    if (walkStarted(n.children)) return true;
  }
  return false;
}

export function isPlanPaused(
  plan: Pick<ChatPlan, "status"> | null | undefined,
): boolean {
  return (plan?.status || "").toLowerCase() === "paused";
}

export function isPlanFinished(
  plan: Pick<ChatPlan, "status"> | null | undefined,
): boolean {
  return (plan?.status || "").toLowerCase() === "finished";
}

/** True when the plan has any in-progress/completed work (or finished). */
export function isPlanStarted(
  plan: Pick<ChatPlan, "status" | "nodes" | "todos"> | null | undefined,
  progress?: PlanProgress | null,
): boolean {
  if (!plan) return false;
  if (isPlanFinished(plan)) return true;
  if ((progress?.in_progress ?? 0) > 0 || (progress?.completed ?? 0) > 0) return true;
  if (plan.nodes?.length) return walkStarted(plan.nodes);
  return (plan.todos || []).some((t) => t.status === "in_progress" || t.status === "completed");
}

/**
 * Structure/content edits blocked while playing after start (or finished).
 * Pause unlocks unfinished work; completed nodes stay frozen in the editor/API.
 */
export function isPlanStructureLocked(
  plan: Pick<ChatPlan, "status" | "nodes" | "todos"> | null | undefined,
  progress?: PlanProgress | null,
): boolean {
  if (!plan) return false;
  if (isPlanFinished(plan)) return true;
  if (isPlanPaused(plan)) return false;
  return isPlanStarted(plan, progress);
}

export function isNodeDone(node: Pick<PlanNode, "status"> | null | undefined): boolean {
  const s = node?.status;
  return s === "completed" || s === "cancelled";
}

export function nodeKind(node: PlanNode): "step" | "subplan" {
  if (node.kind === "step" || node.kind === "subplan") return node.kind;
  return node.children?.length ? "subplan" : "step";
}
