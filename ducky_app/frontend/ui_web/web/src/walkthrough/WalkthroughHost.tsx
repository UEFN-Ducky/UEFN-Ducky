/**
 * Mounts the product walkthrough overlay, registers builtin + plugin tours,
 * hydrates completion flags, and auto-starts `app.shell` when a project is open.
 */
import { useEffect, useRef } from "react";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { registerBuiltinTours } from "./builtinTours";
import { installWalkthroughPersistence, whenWalkthroughHydrated } from "./persistence";
import {
  parsePluginWalkthroughs,
  pluginManifestToTour,
  pluginTourId,
} from "./pluginWalkthroughs";
import {
  autoStartPending,
  isCompleted,
  markTourCompleted,
  registerTour,
  startTour,
  unregisterTour,
} from "./WalkthroughService";
import { WalkthroughOverlay } from "./WalkthroughOverlay";

const registeredPluginTourIds = new Set<string>();

export function WalkthroughHost({ hasProject }: { hasProject: boolean }) {
  const contrib = usePluginContributions();
  const prevEnabled = useRef<Set<string> | null>(null);
  const shellArmed = useRef(false);

  useEffect(() => {
    registerBuiltinTours(registerTour);
    return installWalkthroughPersistence();
  }, []);

  // Sync plugin walkthrough defs from contributions.
  useEffect(() => {
    if (!contrib.ready) return;
    const rows = parsePluginWalkthroughs(contrib.walkthroughs);
    const nextIds = new Set<string>();
    for (const row of rows) {
      const tour = pluginManifestToTour(row);
      if (!tour) continue;
      registerTour(tour);
      nextIds.add(tour.id);
      registeredPluginTourIds.add(tour.id);
    }
    for (const id of [...registeredPluginTourIds]) {
      if (!nextIds.has(id) && id.startsWith("plugin.")) {
        unregisterTour(id);
        registeredPluginTourIds.delete(id);
      }
    }
  }, [contrib]);

  // First-enable: when a plugin appears in enabled_ids, start its tour once.
  useEffect(() => {
    if (!contrib.ready) return;
    const enabled = new Set(contrib.enabled_ids.map((id) => id.trim().toLowerCase()).filter(Boolean));
    const prev = prevEnabled.current;
    prevEnabled.current = enabled;
    if (!prev) return; // skip initial hydrate — only react to transitions
    for (const id of enabled) {
      if (prev.has(id)) continue;
      const tourId = pluginTourId(id);
      if (isCompleted(tourId)) continue;
      const rows = parsePluginWalkthroughs(contrib.walkthroughs);
      const row = rows.find(
        (r) =>
          pluginTourId(String(r.id || r.plugin_id || "")) === tourId &&
          r.auto_start !== "never",
      );
      if (!row) continue;
      markTourCompleted(tourId);
      window.setTimeout(() => {
        void startTour(tourId, { force: true });
      }, 500);
    }
  }, [contrib]);

  // Auto-start app.shell only after completion flags are loaded from disk.
  useEffect(() => {
    if (!hasProject) {
      shellArmed.current = false;
      return;
    }
    if (shellArmed.current) return;
    shellArmed.current = true;
    let cancelled = false;
    void (async () => {
      await whenWalkthroughHydrated();
      if (cancelled) return;
      // Small delay so shell targets (header/docks) finish mounting.
      await new Promise((r) => window.setTimeout(r, 400));
      if (cancelled) return;
      autoStartPending();
    })();
    return () => {
      cancelled = true;
    };
  }, [hasProject]);

  return <WalkthroughOverlay />;
}

/** Call after Store enable succeeds so a tour can start even if contrib refresh is slow. */
export function maybeStartPluginWalkthrough(pluginId: string): void {
  const tourId = pluginTourId(pluginId);
  if (isCompleted(tourId)) return;
  markTourCompleted(tourId);
  window.setTimeout(() => {
    void startTour(tourId, { force: true });
  }, 600);
}
