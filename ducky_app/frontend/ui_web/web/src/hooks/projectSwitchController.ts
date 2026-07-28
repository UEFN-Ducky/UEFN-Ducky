export type ProjectSwitch = { from: string; to: string };
type ResetHandler = (info: ProjectSwitch) => void;

const handlers = new Set<ResetHandler>();
let lastRoot = "";

function normRoot(path: string): string {
  return path.trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

/**
 * Single authority for "the active project changed". Every per-project client cache
 * (diagnostics registry, verse-lsp session, file index…) registers ONE reset handler
 * here instead of each re-deriving "did the root change?" from its own prop-drilled
 * effect. Handlers fire from the earliest signal — whichever of poll/push notices the
 * new root first — synchronously, before the new project's data loads, so no panel can
 * keep showing the previous project's files.
 */
export function onProjectSwitch(handler: ResetHandler): () => void {
  handlers.add(handler);
  return () => {
    handlers.delete(handler);
  };
}

/**
 * Feed the current active-project root here whenever it becomes known (poll or push).
 * The first non-empty value only seeds — a first project load is not a "switch" and must
 * NOT tear down the session the editor just started. Only a change away from a real
 * previous project fires the reset handlers.
 */
export function observeProjectRoot(root: string): void {
  const next = normRoot(root);
  if (next === normRoot(lastRoot)) return;
  const from = lastRoot;
  lastRoot = root;
  if (!from) return; // initial seed (no prior project) — nothing to reset
  for (const handler of [...handlers]) {
    try {
      handler({ from, to: root });
    } catch {
      // a broken reset handler must not block the others
    }
  }
}

export function currentObservedRoot(): string {
  return lastRoot;
}
