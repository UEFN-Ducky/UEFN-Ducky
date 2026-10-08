/**
 * Mounts the product walkthrough overlay, registers builtin + plugin tours,
 * hydrates completion flags, and auto-starts `app.shell` when a project is open.
 */
import { useEffect, useRef, useState } from "react";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { installAgentEventBus, subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { registerBuiltinTours } from "./builtinTours";
import { installWalkthroughPersistence, whenWalkthroughHydrated } from "./persistence";
import {
  expandGatewayManifest,
  parsePluginWalkthroughs,
  pluginManifestToTour,
} from "./pluginWalkthroughs";
import { isCompleted, registerTour, startAppShellIfNeeded, unregisterTour } from "./WalkthroughService";
import { WalkthroughOverlay } from "./WalkthroughOverlay";
import { openCodingAgentLoginUi } from "./openCodingAgentLogin";
import { newlyEnabledForWalkthrough, rememberEnabledPlugin } from "./firstEnable";
import { whenFirstRunSetupDone } from "./firstRunSetup";

const registeredPluginTourIds = new Set<string>();

export function WalkthroughHost() {
  const contrib = usePluginContributions();
  const shellArmed = useRef(false);
  const [tourAttempt, setTourAttempt] = useState(0);

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

  // Seed first-enable memory only. Installing or enabling a plugin must not
  // route the user into a tour — replay stays on the Store card.
  useEffect(() => {
    if (!contrib.ready) return;
    newlyEnabledForWalkthrough(contrib.enabled_ids);
  }, [contrib]);

  // First open starts Welcome even with no island. A failed start stays
  // unarmed so the next attempt can retry.
  useEffect(() => {
    if (shellArmed.current) return;
    let cancelled = false;
    void (async () => {
      await whenWalkthroughHydrated();
      if (cancelled) return;
      // A new install sets up plugins and AI first; the tour of the app comes after.
      await whenFirstRunSetupDone();
      if (cancelled) return;
      // Small delay so shell targets (header/docks) finish mounting.
      await new Promise((r) => window.setTimeout(r, 400));
      if (cancelled) return;
      if (isCompleted("app.shell")) {
        shellArmed.current = true;
        return;
      }
      const started = await startAppShellIfNeeded();
      if (cancelled) return;
      if (started) {
        shellArmed.current = true;
        return;
      }
      if (tourAttempt < 3) {
        window.setTimeout(() => {
          if (!cancelled) setTourAttempt((n) => n + 1);
        }, 500);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [tourAttempt]);

  return <WalkthroughOverlay />;
}

/** Remember the slug so a later contrib refresh cannot look like first-enable. */
export function maybeStartPluginWalkthrough(pluginId: string): void {
  rememberEnabledPlugin(pluginId);
}
