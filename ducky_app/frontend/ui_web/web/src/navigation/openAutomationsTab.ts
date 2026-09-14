/**
 * Open the Automations tab — one host-wide workflow editor.
 *
 * Same singleton pattern as Ledger: header Clock, Tools menu, and
 * `ducky_ui_navigate("automations")` all reach ChatView's opener.
 */

let openAutomationsTabFn: (() => void) | null = null;

export function registerOpenAutomationsTab(fn: () => void): () => void {
  openAutomationsTabFn = fn;
  return () => {
    if (openAutomationsTabFn === fn) openAutomationsTabFn = null;
  };
}

export function requestOpenAutomationsTab(): void {
  openAutomationsTabFn?.();
}
