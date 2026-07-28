/** Bind active appearance.skin to host chrome portal slots (React-owned only). */

import { useEffect, useRef } from "react";
import { useAppearance } from "./AppearanceContext";
import { parsePluginSkinId } from "./appearancePluginIds";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { PLUGIN_UI_ROUTE_PREFIX } from "../plugin-ui/constants";
import { handlePluginFault } from "../plugin-ui/pluginCrashGuard";

export type AppearanceSkinSlots = {
  frame: HTMLElement;
  header: HTMLElement;
  left: HTMLElement;
  right: HTMLElement;
};

declare global {
  interface Window {
    __duckyAppearanceSkinCleanups?: Record<string, () => void>;
    __duckyAppearanceSkinMount?: {
      slots: AppearanceSkinSlots;
      key: string;
      pluginId: string;
      skinId: string;
    };
  }
}

/** Only use slots React already rendered — never create/reparent (breaks removeChild). */
function resolveSkinSlots(): AppearanceSkinSlots | null {
  const frame = document.getElementById("ducky-skin-frame");
  const header = document.getElementById("ducky-skin-header");
  const left = document.getElementById("ducky-skin-left");
  const right = document.getElementById("ducky-skin-right");
  if (!frame || !header || !left || !right) return null;
  return { frame, header, left, right };
}

function runSkinCleanup(key: string): void {
  const bag = window.__duckyAppearanceSkinCleanups;
  const fn = bag?.[key];
  if (typeof fn === "function") {
    try {
      fn();
    } catch (err) {
      console.warn("[uefn appearance.skin] cleanup failed", key, err);
    }
    delete bag![key];
  }
}

function assetUrl(pluginId: string, entry: string): string {
  return `/${PLUGIN_UI_ROUTE_PREFIX}/${pluginId}/${entry.replace(/^\/+/, "")}?t=${Date.now()}`;
}

export function AppearanceSkinBridge() {
  const { skinId, appearanceReady } = useAppearance();
  const contrib = usePluginContributions();
  const scriptRef = useRef<HTMLScriptElement | null>(null);
  const cssRef = useRef<HTMLLinkElement | null>(null);
  const activeKeyRef = useRef("");

  useEffect(() => {
    if (!appearanceReady) return;

    let cancelled = false;
    let retryTimer = 0;

    const tearDown = () => {
      if (retryTimer) {
        window.clearTimeout(retryTimer);
        retryTimer = 0;
      }
      if (activeKeyRef.current) {
        runSkinCleanup(activeKeyRef.current);
        activeKeyRef.current = "";
      }
      if (scriptRef.current) {
        scriptRef.current.remove();
        scriptRef.current = null;
      }
      if (cssRef.current) {
        cssRef.current.remove();
        cssRef.current = null;
      }
      delete window.__duckyAppearanceSkinMount;
      for (const id of ["ducky-skin-frame", "ducky-skin-header", "ducky-skin-left", "ducky-skin-right"]) {
        const el = document.getElementById(id);
        if (el) el.replaceChildren();
      }
      document.body.classList.remove("appearance-skin-active");
    };

    const nextId = (skinId || "").trim();
    if (!nextId) {
      tearDown();
      return () => {
        cancelled = true;
        tearDown();
      };
    }

    const parsed = parsePluginSkinId(nextId);
    if (!parsed) {
      tearDown();
      return () => {
        cancelled = true;
        tearDown();
      };
    }

    const skin = contrib.appearance_skins.find(
      (s) => s.plugin_id === parsed.pluginId && s.id === parsed.skinId,
    );
    if (!skin) {
      tearDown();
      return () => {
        cancelled = true;
        tearDown();
      };
    }

    const mountInto = (slots: AppearanceSkinSlots) => {
      if (cancelled) return;
      tearDown();
      if (cancelled) return;
      document.body.classList.add("appearance-skin-active");
      const key = `${skin.plugin_id}::${skin.id}`;
      activeKeyRef.current = key;
      window.__duckyAppearanceSkinCleanups = window.__duckyAppearanceSkinCleanups || {};
      window.__duckyAppearanceSkinMount = {
        slots,
        key,
        pluginId: skin.plugin_id,
        skinId: skin.id,
      };

      if (skin.css) {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = assetUrl(skin.plugin_id, skin.css);
        link.dataset.uefnAppearanceSkinCss = key;
        document.head.appendChild(link);
        cssRef.current = link;
      }

      const script = document.createElement("script");
      script.src = assetUrl(skin.plugin_id, skin.entry);
      script.async = true;
      script.dataset.uefnAppearanceSkin = key;
      script.onerror = () => {
        console.warn("[uefn appearance.skin] failed to load", skin.plugin_id, skin.entry);
        void handlePluginFault({
          pluginId: skin.plugin_id,
          surface: "skin",
          kind: "theme",
          message: `Failed to load skin script: ${skin.entry}`,
        });
      };
      document.head.appendChild(script);
      scriptRef.current = script;
    };

    let tries = 0;
    const tryMount = () => {
      if (cancelled) return;
      const slots = resolveSkinSlots();
      if (slots) {
        mountInto(slots);
        return;
      }
      // Dock left/right portals appear after workspace mounts — wait briefly.
      if (tries++ < 50) {
        retryTimer = window.setTimeout(tryMount, 100);
      }
    };
    tryMount();

    return () => {
      cancelled = true;
      tearDown();
    };
  }, [appearanceReady, skinId, contrib.appearance_skins, contrib.enabled_ids]);

  return null;
}

