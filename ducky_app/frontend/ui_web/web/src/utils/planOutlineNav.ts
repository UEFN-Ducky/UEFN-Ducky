import type { ChatPlan, PlanNode, PlanProgress } from "../types/panel";

/** Path from root to the node with `targetId` (inclusive). */
export function findNodePath(nodes: PlanNode[] | undefined, targetId: string): PlanNode[] | null {
  const tid = (targetId || "").trim();
  if (!tid) return null;
  const walk = (list: PlanNode[], path: PlanNode[]): PlanNode[] | null => {
    for (const n of list) {
      const next = [...path, n];
      if (n.id === tid) return next;
      const found = walk(n.children || [], next);
      if (found) return found;
    }
    return null;
  };
  return walk(nodes || [], []);
}

/** View a plan as if rooted at `focusId` (that node's children become the outline). */
export function planAtFocus(plan: ChatPlan, focusId: string | null | undefined): ChatPlan {
  const fid = (focusId || "").trim();
  if (!fid) return plan;
  const path = findNodePath(plan.nodes, fid);
  if (!path?.length) return plan;
  const node = path[path.length - 1]!;
  const children = node.children || [];
  return {
    ...plan,
    title: node.content || plan.title,
    overview: "",
    nodes: children,
    todos: children.map((c) => ({
      id: c.id,
      content: c.content,
      status: c.status,
    })),
  };
}

export function progressForNodes(nodes: PlanNode[] | undefined): PlanProgress {
  const flat: PlanNode[] = [];
  const walk = (list: PlanNode[]) => {
    for (const n of list) {
      flat.push(n);
      if (n.children?.length) walk(n.children);
    }
  };
  walk(nodes || []);
  let completed = 0;
  let cancelled = 0;
  let in_progress = 0;
  let pending = 0;
  for (const n of flat) {
    const s = (n.status || "pending").toLowerCase();
    if (s === "completed") completed += 1;
    else if (s === "cancelled") cancelled += 1;
    else if (s === "in_progress") in_progress += 1;
    else pending += 1;
  }
  return { total: flat.length, completed, cancelled, in_progress, pending };
}

function flashEl(el: HTMLElement): void {
  el.classList.remove("is-flash");
  void el.offsetWidth;
  el.classList.add("is-flash");
  window.setTimeout(() => el.classList.remove("is-flash"), 1200);
}

/** Scroll outline row + linked markdown block (or fuzzy text match) into view. */
export function scrollPlanToNode(
  root: HTMLElement | null,
  nodeId: string,
  content: string,
): void {
  if (!root) return;
  const esc =
    typeof CSS !== "undefined" && typeof CSS.escape === "function"
      ? CSS.escape(nodeId)
      : nodeId.replace(/["\\]/g, "\\$&");
  const outline = root.querySelector(".plan-detail-steps, .plan-detail-aside, .plan-todo-card");
  const row = (
    outline
      ? outline.querySelector(`[data-plan-node-id="${esc}"]`)
      : root.querySelector(`[data-plan-node-id="${esc}"]`)
  ) as HTMLElement | null;
  if (row) {
    row.scrollIntoView({ block: "nearest", behavior: "smooth" });
    flashEl(row);
  }
  const md = root.querySelector(".plan-pane-markdown, .plans-tab-detail-md");
  if (md) {
    const anchor = md.querySelector(`[data-plan-node-id="${esc}"]`) as HTMLElement | null;
    if (anchor) {
      const target =
        (anchor.closest("p, h1, h2, h3, h4, li, blockquote") as HTMLElement | null) || anchor;
      const bodyTarget =
        (target.nextElementSibling as HTMLElement | null) &&
        !target.nextElementSibling?.querySelector?.("[data-plan-node-id]")
          ? (target.nextElementSibling as HTMLElement)
          : target;
      bodyTarget.scrollIntoView({ block: "nearest", behavior: "smooth" });
      flashEl(bodyTarget);
      return;
    }
  }
  const needle = (content || "").trim().toLowerCase().slice(0, 48);
  if (!needle || !md) return;
  const candidates = md.querySelectorAll("h1, h2, h3, h4, li, p, strong");
  for (const el of candidates) {
    const text = (el.textContent || "").trim().toLowerCase();
    if (text.includes(needle) || needle.includes(text.slice(0, 48))) {
      (el as HTMLElement).scrollIntoView({ block: "nearest", behavior: "smooth" });
      flashEl(el as HTMLElement);
      break;
    }
  }
}
