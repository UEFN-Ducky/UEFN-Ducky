/**
 * Mounts the product walkthrough overlay, registers builtin + plugin tours,
 * hydrates completion flags, and auto-starts `app.shell` when a project is open.
 */
import { useEffect, useRef } from "react";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { installAgentEventBus, subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { registerBuiltinTours } from "./builtinTours";
import { installWalkthroughPersistence, whenWalkthroughHydrated } from "./persistence";
import {
  expandGatewayManifest,
  parsePluginWalkthroughs,
  pluginManifestToTour,
  pluginTourId,
} from "./pluginWalkthroughs";
import { autoStartPending, isCompleted, markTourCompleted, registerTour, startTour, unregisterTour } from "./WalkthroughService";
import { WalkthroughOverlay } from "./WalkthroughOverlay";
import { openCodingAgentLoginUi } from "./openCodingAgentLogin";
import { newlyEnabledForWalkthrough, rememberEnabledPlugin } from "./firstEnable";
import {
  ensureStarterLlmGateways,
  peekStarterLlmOnboard,
  setSuppressStarterPluginTours,
  shouldSuppressPluginWalkthrough,
} from "./starterLlmGateways";

const registeredPluginTourIds = new Set<string>();

export function WalkthroughHost({ hasProject }: { hasProject: boolean }) {
  const contrib = usePluginContributions();
  const shellArmed = useRef(false);

  useEffect(() => {
    registerBuiltinTours(registerTour);
    return installWalkthroughPersistence();
  }, []);

  useEffect(() => {
    installAgentEventBus();
    return subscribeAgentEvents((event) => {
      if (event.type !== "open_coding_agent_login") return;
      void openCodingAgentLoginUi({
        providerId: event.provider_id || "anthropic",
        title: event.title,
        body: event.text,
      });
    });
  }, []);

  // Sync plugin walkthrough defs from contributions.
  useEffect(() => {
    if (!contrib.ready) return;
    const rows = parsePluginWalkthroughs(contrib.walkthroughs);
    const nextIds = new Set<string>();
    for (const row of rows) {
      const tour = pluginManifestToTour(expandGatewayManifest(row, contrib));
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

  // First-enable: one new slug after the session snapshot. Store updates that
  // drop/reload enabled_ids must not look like a brand-new Enable.
  useEffect(() => {
    if (!contrib.ready) return;
    const fresh = newlyEnabledForWalkthrough(contrib.enabled_ids);
    for (const id of fresh) {
      if (shouldSuppressPluginWalkthrough(id)) continue;
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
        void startTour(tourId);
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
      const onboard = await peekStarterLlmOnboard();
      if (cancelled) return;
      if (onboard.pending) {
        setSuppressStarterPluginTours(true);
        void ensureStarterLlmGateways();
        if (!isCompleted("app.shell")) {
          await startTour("app.shell");
          return;
        }
      }
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
  if (shouldSuppressPluginWalkthrough(pluginId)) return;
  rememberEnabledPlugin(pluginId);
  const tourId = pluginTourId(pluginId);
  if (isCompleted(tourId)) return;
  markTourCompleted(tourId);
  window.setTimeout(() => {
    void startTour(tourId, { force: true });
  }, 600);
}
