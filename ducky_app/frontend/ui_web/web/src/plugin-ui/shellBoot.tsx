/** Load enabled plugins' contributes.shell.boot scripts into the main window. */

import { useEffect, useRef } from "react";
import { getApi } from "../hooks/usePanelApi";
import { patchPluginUiPrefs } from "../hooks/usePluginUiPrefs";
import { usePluginContributions, type PluginShellBoot } from "../hooks/usePluginContributions";
import { PLUGIN_UI_ROUTE_PREFIX } from "../plugin-ui/constants";
import { scrubPluginShellInterference, setBrowserUiCover } from "./bridge";
import {
  mountChoiceDropdown as mountHostChoiceDropdown,
  type MountChoiceDropdownOpts,
  type MountedChoiceDropdown,
} from "./mountChoiceDropdown";
import { pluginLlmCompleteAsync } from "./pluginLlmAsync";
import { pluginTranslateBatchAsync } from "./pluginTranslateAsync";
import { handlePluginFault } from "./pluginCrashGuard";

// Ensure shell.boot can hide native WebView2 panes under modals.
if (typeof window !== "undefined") {
  window.__duckySetBrowserUiCover = setBrowserUiCover;
}

const PREFS_KEY = "uefn-plugin-ui-prefs";
const HOST_KEY = "__duckyPluginHost";

type PrefsBag = Record<string, Record<string, unknown>>;

function readAllPrefs(): PrefsBag {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" ? (parsed as PrefsBag) : {};
  } catch {
    return {};
  }
}

function writePluginPrefs(pluginId: string, slot: Record<string, unknown>): void {
  const all = readAllPrefs();
  all[pluginId] = slot;
  localStorage.setItem(PREFS_KEY, JSON.stringify(all));
  const api = getApi();
  if (api?.plugin_prefs_set) {
    void api.plugin_prefs_set(pluginId, slot).catch(() => undefined);
  }
}

async function hydratePluginPrefsFromDisk(): Promise<void> {
  const api = getApi();
  if (!api?.plugin_prefs_get_all) return;
  try {
    const res = await api.plugin_prefs_get_all();
    if (!res?.ok || !res.prefs || typeof res.prefs !== "object") return;
    const local = readAllPrefs();
    const merged: PrefsBag = { ...local };
    for (const [pid, slot] of Object.entries(res.prefs)) {
      if (!pid || !slot || typeof slot !== "object") continue;
      merged[pid] = { ...(merged[pid] || {}), ...(slot as Record<string, unknown>) };
    }
    localStorage.setItem(PREFS_KEY, JSON.stringify(merged));
  } catch {
    /* ignore */
  }
}

function makeHostApi(pluginId: string) {
  const pid = pluginId.trim().toLowerCase();
  return Object.freeze({
    pluginId: pid,
    prefs: {
      get(): Record<string, unknown> {
        return { ...(readAllPrefs()[pid] || {}) };
      },
      set(patch: Record<string, unknown>): void {
        const slot = { ...(readAllPrefs()[pid] || {}), ...patch };
        writePluginPrefs(pid, slot);
        window.dispatchEvent(
          new CustomEvent("uefn-plugin-prefs", { detail: { pluginId: pid } }),
        );
      },
      /** Load AppData prefs before shell.boot syncs language. */
      async hydrate(): Promise<void> {
        await hydratePluginPrefsFromDisk();
      },
    },
    llm: {
      async batchComplete(opts: {
        system?: string;
        user: string;
        model?: string;
      }): Promise<{ ok: boolean; text?: string; error?: string }> {
        // Legacy raw LLM — prefer translate.batch (MCP translate_ui_batch path).
        return pluginLlmCompleteAsync(pid, opts.system || "", opts.user, opts.model || "", 180_000);
      },
    },
    /** Same pipeline as MCP tool translate_ui_batch — Languages UI should use this. */
    translate: {
      async batch(opts: {
        language: string;
        strings: Record<string, string>;
        model?: string;
      }): Promise<{
        ok: boolean;
        map?: Record<string, string>;
        missing?: string[];
        error?: string;
        language?: string;
      }> {
        return pluginTranslateBatchAsync(
          pid,
          opts.language || "",
          opts.strings || {},
          opts.model || "",
          180_000,
        );
      },
    },
    cache: {
      async get(key: string): Promise<Record<string, unknown>> {
        const api = getApi();
        if (!api?.plugin_cache_get) return {};
        const res = await api.plugin_cache_get(pid, key);
        return res?.ok && res.data && typeof res.data === "object" ? (res.data as Record<string, unknown>) : {};
      },
      async set(key: string, data: Record<string, unknown>): Promise<void> {
        const api = getApi();
        if (api?.plugin_cache_set) await api.plugin_cache_set(pid, key, data);
      },
      async clear(key?: string): Promise<void> {
        const api = getApi();
        if (api?.plugin_cache_clear) await api.plugin_cache_clear(pid, key || "");
      },
    },
    /** Core ChoiceDropdown (radio/checkbox panel) for shell.boot UIs. */
    mountChoiceDropdown(
      container: HTMLElement,
      opts: MountChoiceDropdownOpts,
    ): MountedChoiceDropdown {
      return mountHostChoiceDropdown(container, opts);
    },
  });
}

declare global {
  interface Window {
    [HOST_KEY]?: ReturnType<typeof makeHostApi> & {
      forPlugin?: (pluginId: string) => ReturnType<typeof makeHostApi>;
      mountChoiceDropdown?: (
        container: HTMLElement,
        opts: MountChoiceDropdownOpts,
      ) => MountedChoiceDropdown;
    };
    __duckyPluginBootCleanups?: Record<string, () => void>;
  }
}

function runBootCleanup(pluginId: string): void {
  const pid = (pluginId || "").trim().toLowerCase();
  // Translation disable/uninstall: force default language even if boot cleanup throws
  // or the script never registered (keep-data must not leave UI stuck translated).
  if (pid === "translation") {
    try {
      patchPluginUiPrefs("translation", { language: "en" });
    } catch (err) {
      console.warn("[uefn shell.boot] translation default reset failed", err);
    }
  }
  const bag = window.__duckyPluginBootCleanups;
  const fn = bag?.[pid] ?? bag?.[pluginId];
  if (typeof fn === "function") {
    try {
      fn();
    } catch (err) {
      console.warn("[uefn shell.boot] cleanup failed", pluginId, err);
    }
    delete bag![pid];
    if (pluginId && bag && pluginId in bag) delete bag[pluginId];
  }
}

function bootUrl(boot: PluginShellBoot): string {
  const entry = boot.entry.replace(/^\/+/, "");
  return `/${PLUGIN_UI_ROUTE_PREFIX}/${boot.plugin_id}/${entry}?t=${Date.now()}`;
}

/** Mount shell.boot scripts for enabled plugins; unload when disabled. */
export function usePluginShellBoots(): void {
  const contrib = usePluginContributions();
  const loaded = useRef(new Map<string, HTMLScriptElement>());

  // Escape hatch: stuck browser/settings overlays + native panes must not brick tabs.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const stuck =
        !!document.querySelector(".browser-settings-overlay") ||
        !!document.querySelector(".browser-header-menu");
      if (!stuck && !e.shiftKey) return;
      scrubPluginShellInterference();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  useEffect(() => {
    const first = contrib.shell_boots[0];
    const defaultApi = first ? makeHostApi(first.plugin_id) : null;
    // One frozen root — never Object.assign onto a frozen makeHostApi() result.
    window[HOST_KEY] = Object.freeze({
      pluginId: defaultApi?.pluginId ?? "",
      prefs: defaultApi?.prefs ?? {
        get: () => ({}),
        set: () => undefined,
        hydrate: async () => undefined,
      },
      llm: defaultApi?.llm ?? {
        batchComplete: async () => ({ ok: false, error: "no shell.boot plugin" }),
      },
      cache: defaultApi?.cache ?? {
        get: async () => ({}),
        set: async () => undefined,
        clear: async () => undefined,
      },
      mountChoiceDropdown: (
        container: HTMLElement,
        opts: MountChoiceDropdownOpts,
      ): MountedChoiceDropdown => mountHostChoiceDropdown(container, opts),
      forPlugin: (pluginId: string) => makeHostApi(pluginId),
    }) as Window[typeof HOST_KEY];

    const wanted = new Set(contrib.shell_boots.map((b) => `${b.plugin_id}::${b.entry}`));

    for (const [key, el] of [...loaded.current.entries()]) {
      if (!wanted.has(key)) {
        const pluginId = key.split("::")[0] || "";
        runBootCleanup(pluginId);
        el.remove();
        loaded.current.delete(key);
      }
    }

    for (const boot of contrib.shell_boots) {
      const key = `${boot.plugin_id}::${boot.entry}`;
      if (loaded.current.has(key)) continue;
      const script = document.createElement("script");
      script.src = bootUrl(boot);
      script.async = true;
      script.dataset.uefnShellBoot = key;
      script.dataset.pluginId = boot.plugin_id;
      // Isolate load failures — never let one plugin's 404 break the shell.
      script.onerror = () => {
        console.warn("[uefn shell.boot] failed to load", boot.plugin_id, boot.entry);
        try {
          script.remove();
        } catch {
          /* ignore */
        }
        loaded.current.delete(key);
        void handlePluginFault({
          pluginId: boot.plugin_id,
          surface: "shell.boot",
          kind: "plugin",
          message: `Failed to load shell.boot: ${boot.entry}`,
        });
      };
      document.head.appendChild(script);
      loaded.current.set(key, script);
    }
  }, [contrib.shell_boots, contrib.enabled_ids]);
}

export function PluginShellBootBridge() {
  usePluginShellBoots();
  return null;
}
