/** Mount `#ducky-fx-root` and run the active built-in / plugin appearance effect. */

import { useEffect, useRef } from "react";
import { useAppearance } from "./AppearanceContext";
import { MATRIX_EFFECT_ID, mountMatrixFx } from "./matrixFx";
import { parsePluginEffectId } from "./appearancePluginIds";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { PLUGIN_UI_ROUTE_PREFIX } from "../plugin-ui/constants";
import { handlePluginFault } from "../plugin-ui/pluginCrashGuard";

declare global {
  interface Window {
    __duckyAppearanceFxCleanups?: Record<string, () => void>;
    /** Set while a plugin effect script loads — root + cleanup key. */
    __duckyAppearanceFxMount?: {
      root: HTMLElement;
      key: string;
      pluginId: string;
      effectId: string;
    };
  }
}

function ensureFxRoot(): HTMLElement {
  let el = document.getElementById("ducky-fx-root");
  if (!el) {
    el = document.createElement("div");
    el.id = "ducky-fx-root";
    el.setAttribute("aria-hidden", "true");
    document.body.prepend(el);
  }
  return el;
}

function runFxCleanup(key: string): void {
  const bag = window.__duckyAppearanceFxCleanups;
  const fn = bag?.[key];
  if (typeof fn === "function") {
    try {
      fn();
    } catch (err) {
      console.warn("[uefn appearance.effects] cleanup failed", key, err);
    }
    delete bag![key];
  }
}

function effectScriptUrl(pluginId: string, entry: string): string {
  const path = entry.replace(/^\/+/, "");
  return `/${PLUGIN_UI_ROUTE_PREFIX}/${pluginId}/${path}?t=${Date.now()}`;
}

export function AppearanceFxBridge() {
  const { effectId, effectsEnabled, appearanceReady } = useAppearance();
  const contrib = usePluginContributions();
  const cleanupRef = useRef<(() => void) | null>(null);
  const scriptRef = useRef<HTMLScriptElement | null>(null);
  const activeKeyRef = useRef<string>("");

  useEffect(() => {
    if (!appearanceReady) return;
    const root = ensureFxRoot();
    const nextId = effectsEnabled ? (effectId || "").trim() : "";
    const active = !!nextId;
    document.body.classList.toggle("appearance-fx-active", active);

    const tearDown = () => {
      if (cleanupRef.current) {
        cleanupRef.current();
        cleanupRef.current = null;
      }
      if (activeKeyRef.current) {
        runFxCleanup(activeKeyRef.current);
        activeKeyRef.current = "";
      }
      if (scriptRef.current) {
        scriptRef.current.remove();
        scriptRef.current = null;
      }
      root.replaceChildren();
    };

    tearDown();

    if (!nextId) {
      return () => {
        document.body.classList.remove("appearance-fx-active");
        tearDown();
      };
    }

    if (nextId === MATRIX_EFFECT_ID) {
      cleanupRef.current = mountMatrixFx(root);
      return () => {
        document.body.classList.remove("appearance-fx-active");
        tearDown();
      };
    }

    const parsed = parsePluginEffectId(nextId);
    if (!parsed) {
      document.body.classList.remove("appearance-fx-active");
      return () => tearDown();
    }

    const effect = contrib.appearance_effects.find(
      (e) => e.plugin_id === parsed.pluginId && e.id === parsed.effectId,
    );
    if (!effect) {
      document.body.classList.remove("appearance-fx-active");
      return () => tearDown();
    }

    const key = `${effect.plugin_id}::${effect.id}`;
    activeKeyRef.current = key;
    window.__duckyAppearanceFxCleanups = window.__duckyAppearanceFxCleanups || {};
    window.__duckyAppearanceFxMount = {
      root,
      key,
      pluginId: effect.plugin_id,
      effectId: effect.id,
    };

    const script = document.createElement("script");
    script.src = effectScriptUrl(effect.plugin_id, effect.entry);
    script.async = true;
    script.dataset.uefnAppearanceFx = key;
    script.onerror = () => {
      console.warn("[uefn appearance.effects] failed to load", effect.plugin_id, effect.entry);
      void handlePluginFault({
        pluginId: effect.plugin_id,
        surface: "fx",
        kind: "theme",
        message: `Failed to load effect script: ${effect.entry}`,
      });
    };
    document.head.appendChild(script);
    scriptRef.current = script;

    return () => {
      document.body.classList.remove("appearance-fx-active");
      delete window.__duckyAppearanceFxMount;
      tearDown();
    };
  }, [appearanceReady, effectId, effectsEnabled, contrib.appearance_effects, contrib.enabled_ids]);

  return null;
}
