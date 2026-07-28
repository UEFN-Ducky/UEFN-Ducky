import { onApiReady } from "../hooks/onApiReady";
import { getApi } from "../hooks/usePanelApi";
import {
  setCompletedMap,
  setWalkthroughPersist,
} from "./WalkthroughService";

function normalizeMap(raw: unknown): Record<string, boolean> {
  if (!raw || typeof raw !== "object") return {};
  const out: Record<string, boolean> = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (v) out[k] = true;
  }
  return out;
}

let hydrateResolve: (() => void) | null = null;
let hydratePromise: Promise<void> | null = null;
let hydrated = false;

function ensureHydratePromise(): Promise<void> {
  if (hydrated) return Promise.resolve();
  if (!hydratePromise) {
    hydratePromise = new Promise<void>((resolve) => {
      hydrateResolve = resolve;
    });
  }
  return hydratePromise;
}

function markHydrated(): void {
  hydrated = true;
  hydrateResolve?.();
  hydrateResolve = null;
}

/** Resolves once completion flags have been loaded from PanelSettings (or load failed). */
export function whenWalkthroughHydrated(): Promise<void> {
  return ensureHydratePromise();
}

/** Load completed map from PanelSettings and wire save_agent_settings persistence. */
export function installWalkthroughPersistence(): () => void {
  let cancelled = false;
  hydrated = false;
  hydratePromise = null;
  hydrateResolve = null;
  ensureHydratePromise();

  setWalkthroughPersist((map) => {
    const api = getApi();
    if (!api?.save_agent_settings) return;
    void api.save_agent_settings({ walkthrough_completed: map });
  });

  const unsub = onApiReady(() => {
    void (async () => {
      const api = getApi();
      if (!api?.get_settings || cancelled) {
        markHydrated();
        return;
      }
      try {
        const s = await api.get_settings();
        if (cancelled) return;
        setCompletedMap(normalizeMap((s as { walkthrough_completed?: unknown }).walkthrough_completed));
      } catch {
        /* ignore — still mark hydrated so auto-start is not blocked forever */
      } finally {
        if (!cancelled) markHydrated();
      }
    })();
  });

  return () => {
    cancelled = true;
    unsub();
    setWalkthroughPersist(null);
    markHydrated();
  };
}
