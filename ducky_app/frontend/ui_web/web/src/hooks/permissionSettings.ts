import { getApi } from "./usePanelApi";

export const PERMISSIONS_CHANGED = "ducky-permissions-changed";

// Permission controls and chat recovery share a queue. Rapid clicks must reach
// disk in order, and a rejected save must not prevent the next change.
let pending: Promise<void> = Promise.resolve();

export function savePermissionSettings(patch: Record<string, unknown>): Promise<void> {
  const save = pending.then(async () => {
    const api = getApi();
    if (!api) throw new Error("Settings unavailable. Change was not saved.");
    const result = await api.save_agent_settings(patch);
    if (typeof result !== "string" || !result.startsWith("Saved")) {
      throw new Error(result || "Change was not saved.");
    }
    window.dispatchEvent(new CustomEvent(PERMISSIONS_CHANGED, { detail: patch }));
  });
  pending = save.catch(() => {});
  return save;
}
