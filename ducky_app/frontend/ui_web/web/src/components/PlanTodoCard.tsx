import { useMemo, useState } from "react";
import type { ChatPlan, PlanNode, PlanProgress, PlanTodoStatus } from "../types/panel";
import { nodeKind } from "../utils/planLock";

export interface PlanTodoCardProps {
  plan: ChatPlan;
  progress?: PlanProgress | null;
  onOpenPlan?: () => void;
  compact?: boolean;
  /** Templates: no N/M progress chrome. */
  hideProgress?: boolean;
  /** Hide the title row when the parent already shows the plan title. */
  hideTitlebar?: boolean;
  /** Click an outline row — drill into subplan or scroll to that step. */
  onSelectNode?: (node: PlanNode) => void;
  /** Highlight this node id briefly / as selected. */
  highlightNodeId?: string | null;
}

type OutlineRow = { label: string; node: PlanNode; depth: number };

function flattenOutline(nodes: PlanNode[] | undefined, prefix = ""): OutlineRow[] {
  const out: OutlineRow[] = [];
  (nodes || []).forEach((node, i) => {
    const label = prefix ? `${prefix}.${i + 1}` : `${i + 1}`;
    const depth = prefix ? prefix.split(".").length : 0;
    out.push({ label, node, depth });
    if (node.children?.length) out.push(...flattenOutline(node.children, label));
  });
  return out;
}

function nodesFromPlan(plan: ChatPlan): PlanNode[] {
  if (plan.nodes?.length) return plan.nodes;
  return (plan.todos || []).map((t) => ({
    id: t.id,
    content: t.content,
    status: t.status,
    children: [],
  }));
}

function countProgress(plan: ChatPlan, progress?: PlanProgress | null): { done: number; total: number } {
  if (progress) {
    return { done: progress.completed, total: progress.total };
  }
  const flat = flattenOutline(nodesFromPlan(plan));
  return {
    done: flat.filter((r) => r.node.status === "completed").length,
    total: flat.length,
  };
}

function TodoRow({
  label,
  content,
  status,
  depth,
  nodeId,
  kind,
  clickable,
  highlighted,
  onSelect,
}: {
  label: string;
  content: string;
  status: PlanTodoStatus | string;
  depth: number;
  nodeId: string;
  kind: "step" | "subplan";
  clickable?: boolean;
  highlighted?: boolean;
  onSelect?: () => void;
}) {
  const done = status === "completed" || status === "cancelled";
  const active = status === "in_progress";
  const className = `plan-todo-row${done ? " is-done" : ""}${active ? " is-active" : ""}${
    clickable ? " is-clickable" : ""
  }${highlighted ? " is-highlight" : ""}${kind === "subplan" ? " is-subplan" : " is-step"}`;
  const check = (
    <span className="plan-todo-check" aria-hidden>
      {done ? (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <circle cx="12" cy="12" r="10" />
          <path d="M8 12l2.5 2.5L16 9" />
        </svg>
      ) : active ? (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="10" />
          <circle cx="12" cy="12" r="3" fill="currentColor" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="10" />
        </svg>
      )}
    </span>
  );
  const body = (
    <>
      <span className="plan-todo-outline-n" aria-hidden>
        {label}
      </span>
      <span className={`plan-todo-kind${kind === "subplan" ? " plan-todo-kind--subplan" : ""}`}>
        {kind === "subplan" ? "Subplan" : "Step"}
      </span>
      {check}
      <span className="plan-todo-text">{content}</span>
    </>
  );
  const depthClass = depth ? ` plan-todo-row--d${Math.min(depth, 6)}` : "";
  if (clickable && onSelect) {
    return (
      <button
        type="button"
        className={`${className}${depthClass}`}
        data-plan-node-id={nodeId}
        data-status={status}
        onClick={onSelect}
        title={kind === "subplan" ? "Open this subplan" : "Open this step"}
      >
        {body}
      </button>
    );
  }
  return (
    <div className={`${className}${depthClass}`} data-plan-node-id={nodeId} data-status={status}>
      {body}
    </div>
  );
}

export function PlanTodoCard({
  plan,
  progress,
  onOpenPlan,
  compact = false,
  hideProgress = false,
  hideTitlebar = false,
  onSelectNode,
  highlightNodeId = null,
}: PlanTodoCardProps) {
  const outline = useMemo(() => flattenOutline(nodesFromPlan(plan)), [plan]);
  const { done, total } = useMemo(() => countProgress(plan, progress), [plan, progress]);
  const [expanded, setExpanded] = useState(true);
  const allDone = total > 0 && done >= total;
  const isTemplate = hideProgress || plan.kind === "template";

  return (
    <div
      className={`plan-todo-card${compact ? " plan-todo-card--compact" : ""}${
        hideTitlebar ? " plan-todo-card--aside" : ""
      }`}
    >
      {!hideTitlebar ? (
        <div className="plan-todo-card-titlebar">
          <button type="button" className="plan-todo-card-title-btn" onClick={onOpenPlan} title="Open plan tab">
            {plan.title || "Plan"}
          </button>
        </div>
      ) : null}
      <button
        type="button"
        className="plan-todo-card-header"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span className="plan-todo-card-header-label">
          {isTemplate
            ? `${total} steps`
            : allDone
              ? `${done} of ${total} steps completed`
              : `${done} of ${total} steps`}
        </span>
        <span className={`plan-todo-card-chevron${expanded ? " is-expanded" : ""}`} aria-hidden>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </span>
      </button>
      {expanded ? (
        <div className="plan-todo-card-body">
          {outline.map(({ label, node, depth }) => (
            <TodoRow
              key={node.id}
              label={label}
              content={node.content}
              status={node.status}
              depth={depth}
              nodeId={node.id}
              kind={nodeKind(node)}
              clickable={Boolean(onSelectNode)}
              highlighted={highlightNodeId === node.id}
              onSelect={onSelectNode ? () => onSelectNode(node) : undefined}
            />
          ))}
          {!outline.length ? <div className="plan-todo-empty">No steps yet.</div> : null}
        </div>
      ) : null}
    </div>
  );
}

/** Compat export for callers that still think in flat todos. */
export type { PlanTodoStatus };
