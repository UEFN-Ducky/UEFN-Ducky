/**
 * Open the Changes tab, the project-wide ledger of everything the AI changed.
 *
 * There is exactly one of it, so the opener is a module singleton: the header
 * button, the Context panel's "See all changes" link and `ducky_ui_navigate`
 * all reach the same tab without threading a callback through the tree.
 * ChatView registers the real opener, which focuses the tab in whichever
 * window already has it before opening a new one here.
 */

let openChangesTabFn: (() => void) | null = null;

export function registerOpenChangesTab(fn: () => void): () => void {
  openChangesTabFn = fn;
  return () => {
    if (openChangesTabFn === fn) openChangesTabFn = null;
  };
}

/** No-op before ChatView mounts (no project open — there is no ledger to show). */
export function requestOpenChangesTab(): void {
  openChangesTabFn?.();
}
