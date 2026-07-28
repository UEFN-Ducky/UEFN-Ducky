/**
 * Named app hooks — fire-and-forget CustomEvents.
 * Emitters call emitAppHook(); listeners (e.g. soundFx) subscribe independently.
 */

export const DUCKY_HOOK_EVENT = "ducky:hook";

export type AppHookId =
  | "tab.changed"
  | "settings.opened"
  | "agent.selected"
  | "agent.done"
  | "agent.error"
  | "verse.errors"
  | (string & {});

export interface AppHookDef {
  id: AppHookId;
  label: string;
}

/** Built-in hooks the shell emits. Plugins add more via contributes.hooks. */
export const APP_HOOKS: AppHookDef[] = [
  { id: "tab.changed", label: "Tab changed" },
  { id: "settings.opened", label: "Settings opened" },
  { id: "agent.selected", label: "Agent selected" },
  { id: "agent.done", label: "Agent finished" },
  { id: "agent.error", label: "Agent error" },
  { id: "verse.errors", label: "Verse errors" },
];

export interface AppHookDetail {
  id: AppHookId;
  [key: string]: unknown;
}

export function emitAppHook(hookId: AppHookId, detail?: Record<string, unknown>): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(DUCKY_HOOK_EVENT, {
      detail: { id: hookId, ...detail },
    }),
  );
}

export function subscribeAppHooks(
  handler: (detail: AppHookDetail) => void,
): () => void {
  if (typeof window === "undefined") return () => {};
  const listener = (ev: Event) => {
    const detail = (ev as CustomEvent).detail as AppHookDetail | undefined;
    if (!detail?.id) return;
    handler(detail);
  };
  window.addEventListener(DUCKY_HOOK_EVENT, listener);
  return () => window.removeEventListener(DUCKY_HOOK_EVENT, listener);
}
