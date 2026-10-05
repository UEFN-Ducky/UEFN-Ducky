import { useCallback, useRef, useState } from "react";
import type { AutomationDto } from "../types/panel";

type Update = AutomationDto | null | ((current: AutomationDto | null) => AutomationDto | null);
type Entry = { doc: AutomationDto; label: string };
export function editableWorkflow(doc: AutomationDto) {
  return { name: doc.name, description: doc.description, enabled: doc.enabled, graph: doc.graph };
}
/** Same workflow in the same folder: a move to another owner starts a fresh history. */
function sameWorkflow(a: AutomationDto | null, b: AutomationDto | null): boolean {
  return !!a && !!b && a.id === b.id && (a.owner?.id || "local") === (b.owner?.id || "local");
}
function changeLabel(before: AutomationDto, after: AutomationDto): string {
  if (before.name !== after.name) return "Rename workflow";
  if (before.enabled !== after.enabled) return after.enabled ? "Enable workflow" : "Disable workflow";
  if (before.graph.nodes.length !== after.graph.nodes.length) return after.graph.nodes.length > before.graph.nodes.length ? "Add node" : "Delete node";
  if (JSON.stringify(before.graph.groups) !== JSON.stringify(after.graph.groups)) return "Edit groups";
  if (JSON.stringify(before.graph.edges) !== JSON.stringify(after.graph.edges)) return "Edit connections";
  if (before.graph.nodes.some((node, i) => node.x !== after.graph.nodes[i]?.x || node.y !== after.graph.nodes[i]?.y)) return "Move nodes";
  const changed = before.graph.nodes.findIndex((node, i) => JSON.stringify(node) !== JSON.stringify(after.graph.nodes[i]));
  const was = before.graph.nodes[changed], now = after.graph.nodes[changed];
  if (was && now && was.type !== now.type) return now.type === "code.js" ? "Edit as custom code" : was.type === "code.js" ? "Revert to built-in" : "Edit node";
  if (was && now && now.type === "code.js" && was.config.code !== now.config.code) return "Edit code";
  return "Edit node";
}

/** One pointer gesture or focused field edit is one undo step. Server acknowledgements are neutral. */
export function useWorkflowHistory() {
  const [draft, render] = useState<AutomationDto | null>(null);
  const current = useRef<AutomationDto | null>(null);
  const journal = useRef<{ entries: Entry[]; index: number; batching: boolean; changed: boolean }>({ entries: [], index: -1, batching: false, changed: false });
  const end = useCallback(() => { journal.current.batching = false; journal.current.changed = false; }, []);
  const begin = useCallback(() => { journal.current.batching = true; journal.current.changed = false; }, []);
  const reset = useCallback((doc: AutomationDto | null) => {
    current.current = doc;
    journal.current = { entries: doc ? [{ doc, label: "Opened workflow" }] : [], index: doc ? 0 : -1, batching: false, changed: false };
    render(doc);
  }, []);
  const replace = useCallback((update: Update) => {
    const next = typeof update === "function" ? update(current.current) : update;
    if (!sameWorkflow(next, current.current)) { reset(next); return; }
    current.current = next;
    render(next);
  }, [reset]);
  const setDraft = useCallback((update: Update, label?: string) => {
    const before = current.current;
    const next = typeof update === "function" ? update(before) : update;
    if (!before || !next || !sameWorkflow(before, next)) { reset(next); return; }
    if (JSON.stringify(editableWorkflow(before)) === JSON.stringify(editableWorkflow(next))) return;
    const state = journal.current;
    const entry = { doc: next, label: label || changeLabel(before, next) };
    if (state.batching && state.changed) state.entries[state.index] = entry;
    else { state.entries = [...state.entries.slice(0, state.index + 1), entry]; state.index++; }
    state.changed = state.batching;
    current.current = next;
    render(next);
  }, [reset]);
  const go = useCallback((index: number) => {
    const state = journal.current;
    end();
    if (!current.current || index < 0 || index >= state.entries.length || index === state.index) return null;
    state.index = index;
    const next = { ...current.current, ...editableWorkflow(state.entries[index].doc) };
    current.current = next;
    render(next);
    return next;
  }, [end]);
  return { draft, current, setDraft, replace, reset, begin, end, go, entries: journal.current.entries, index: journal.current.index };
}
