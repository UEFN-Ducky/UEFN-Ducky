import { getApi } from "./usePanelApi";

/**
 * Anonymous usage stats from the UI, fire-and-forget. Only `feature.open`, `setup.step`,
 * `setup.ai`, `setup.source`, `setup.rating` and `tour.end` are accepted by the panel; values are
 * enum labels, never text a person typed. The switch lives in Settings → Permissions → Anonymous
 * usage stats.
 */
let lastKey = "";
let lastAt = 0;

export type UiUsageEvent = "feature.open" | "setup.step" | "setup.ai" | "setup.source" | "setup.rating" | "tour.end";

export function usageTrack(name: UiUsageEvent, props: Record<string, string>): void {
  try {
    // React may run a state updater twice in development: the same event within
    // half a second counts once.
    const key = name + JSON.stringify(props);
    const now = Date.now();
    if (key === lastKey && now - lastAt < 500) return;
    lastKey = key;
    lastAt = now;
    const api = getApi();
    const fn = api?.usage_track;
    if (typeof fn !== "function") return;
    void Promise.resolve(fn.call(api, name, props)).catch(() => undefined);
  } catch {
    /* stats never break the UI */
  }
}
