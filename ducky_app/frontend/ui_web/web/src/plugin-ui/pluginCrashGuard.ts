/**
 * Plugin / theme fault isolation — never blank the host App.
 * Plugin UI crash → auto-disable that plugin + banner with plugin id.
 * Theme/skin/fx crash → clear that surface only (plugin stays enabled).
 */

import { getApi } from "../hooks/usePanelApi";
import { PLUGIN_UI_ROUTE_PREFIX } from "./constants";

export type PluginFaultKind = "plugin" | "theme";

export type PluginCrashNotice = {
  id: string;
  pluginId: string;
  surface: string;
  kind: PluginFaultKind;
  message: string;
  action: "disabled" | "theme-cleared";
  ts: number;
};

/** AppearanceProvider listens and clears skin / fx. */
export const THEME_FAULT_EVENT = "ducky-plugin-theme-fault";

export type ThemeFaultDetail = {
  pluginId: string;
  surface: "skin" | "fx" | "css";
};

const PLUGIN_UI_PATH_RE = new RegExp(
  `(?:^|/)${PLUGIN_UI_ROUTE_PREFIX}/([a-z][a-z0-9_-]{0,63})(?:/|$)`,
  "i",
);

let notices: PluginCrashNotice[] = [];
const listeners = new Set<() => void>();
/** Debounce identical faults (pluginId+surface) for a few seconds. */
const recentKeys = new Map<string, number>();
const DEBOUNCE_MS = 4000;
let guardsInstalled = false;

function emitNotices() {
  for (const fn of [...listeners]) fn();
}

export function subscribePluginCrashNotices(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function getPluginCrashNotices(): PluginCrashNotice[] {
  return notices;
}

export function dismissPluginCrashNotice(id: string): void {
  const next = notices.filter((n) => n.id !== id);
  if (next.length === notices.length) return;
  notices = next;
  emitNotices();
}

/** Extract plugin id from a plugin-ui asset URL or script src. */
export function attributePluginFromUrl(url: string | null | undefined): string | null {
  const s = String(url || "").trim();
  if (!s) return null;
  const m = s.match(PLUGIN_UI_PATH_RE);
  return m?.[1]?.toLowerCase() || null;
}

/** Best-effort attribution from ErrorEvent / script tags / stack. */
export function attributePluginFromErrorEvent(ev: {
  filename?: string;
  message?: string;
  error?: { stack?: string } | null;
  target?: EventTarget | null;
}): { pluginId: string; surface: string; kind: PluginFaultKind } | null {
  const target = ev.target;
  if (typeof HTMLScriptElement !== "undefined" && target instanceof HTMLScriptElement) {
    const boot = target.dataset.uefnShellBoot || target.dataset.pluginId;
    if (boot) {
      const pluginId = (target.dataset.pluginId || boot.split("::")[0] || "").toLowerCase();
      if (pluginId) return { pluginId, surface: "shell.boot", kind: "plugin" };
    }
    const skin = target.dataset.uefnAppearanceSkin;
    if (skin) {
      const pluginId = (skin.split("::")[0] || "").toLowerCase();
      if (pluginId) return { pluginId, surface: "skin", kind: "theme" };
    }
    const fx = target.dataset.uefnAppearanceFx;
    if (fx) {
      const pluginId = (fx.split("::")[0] || "").toLowerCase();
      if (pluginId) return { pluginId, surface: "fx", kind: "theme" };
    }
    const fromSrc = attributePluginFromUrl(target.src);
    if (fromSrc) return { pluginId: fromSrc, surface: "script", kind: "plugin" };
  }
  if (typeof HTMLLinkElement !== "undefined" && target instanceof HTMLLinkElement) {
    const css = target.dataset.uefnAppearanceCss || target.dataset.uefnAppearanceSkinCss;
    if (css) {
      const pluginId = (css.split("::")[0] || "").toLowerCase();
      if (pluginId) return { pluginId, surface: "css", kind: "theme" };
    }
    const fromHref = attributePluginFromUrl(target.href);
    if (fromHref) return { pluginId: fromHref, surface: "css", kind: "theme" };
  }

  const fromFile = attributePluginFromUrl(ev.filename);
  if (fromFile) {
    if (typeof window !== "undefined") {
      const skinMount = window.__duckyAppearanceSkinMount;
      if (skinMount?.pluginId?.toLowerCase() === fromFile) {
        return { pluginId: fromFile, surface: "skin", kind: "theme" };
      }
      const fxMount = window.__duckyAppearanceFxMount;
      if (fxMount?.pluginId?.toLowerCase() === fromFile) {
        return { pluginId: fromFile, surface: "fx", kind: "theme" };
      }
    }
    return { pluginId: fromFile, surface: "script", kind: "plugin" };
  }

  const stack = String(ev.error?.stack || ev.message || "");
  const fromStack = attributePluginFromUrl(stack);
  if (fromStack) return { pluginId: fromStack, surface: "script", kind: "plugin" };

  return null;
}

function shouldHandle(key: string): boolean {
  const now = Date.now();
  const prev = recentKeys.get(key) || 0;
  if (now - prev < DEBOUNCE_MS) return false;
  recentKeys.set(key, now);
  // ponytail: Map grows unbounded if many unique keys — cap to last ~50.
  if (recentKeys.size > 50) {
    const oldest = [...recentKeys.entries()].sort((a, b) => a[1] - b[1]).slice(0, 20);
    for (const [k] of oldest) recentKeys.delete(k);
  }
  return true;
}

function pushNotice(notice: PluginCrashNotice): void {
  notices = [notice, ...notices.filter((n) => n.pluginId !== notice.pluginId)].slice(0, 5);
  emitNotices();
}

function clearThemeSurface(pluginId: string, surface: ThemeFaultDetail["surface"]): void {
  try {
    window.dispatchEvent(
      new CustomEvent<ThemeFaultDetail>(THEME_FAULT_EVENT, {
        detail: { pluginId, surface },
      }),
    );
  } catch {
    /* ignore */
  }
}

/**
 * Isolate a plugin/theme fault: disable plugin OR clear theme, log crash, banner.
 * Safe to call from ErrorBoundaries and window error handlers.
 */
export async function handlePluginFault(opts: {
  pluginId: string;
  surface: string;
  kind: PluginFaultKind;
  message: string;
  stack?: string;
  componentStack?: string;
}): Promise<void> {
  const pluginId = String(opts.pluginId || "")
    .trim()
    .toLowerCase();
  if (!pluginId) return;

  const surface = String(opts.surface || "ui").trim() || "ui";
  const key = `${opts.kind}:${pluginId}:${surface}`;
  if (!shouldHandle(key)) return;

  const message = String(opts.message || "unknown error").slice(0, 500);
  console.error(
    `[plugin-fault] ${opts.kind} · ${pluginId} · ${surface}:`,
    message,
    opts.stack || "",
  );

  let action: PluginCrashNotice["action"] = "disabled";
  if (opts.kind === "theme") {
    const themeSurface: ThemeFaultDetail["surface"] =
      surface === "fx" || surface === "css" || surface === "skin" ? surface : "skin";
    clearThemeSurface(pluginId, themeSurface);
    action = "theme-cleared";
  } else {
    const api = getApi();
    try {
      await api?.set_uefn_plugin_enabled?.(pluginId, false);
    } catch (err) {
      console.warn("[plugin-fault] disable failed", pluginId, err);
    }
    action = "disabled";
  }

  pushNotice({
    id: `${Date.now()}-${pluginId}-${surface}`,
    pluginId,
    surface,
    kind: opts.kind,
    message,
    action,
    ts: Date.now(),
  });

  const api = getApi();
  try {
    await api?.report_ui_crash?.({
      label: `plugin:${pluginId}:${surface}`,
      message: `[${opts.kind}/${action}] ${message}`,
      stack: opts.stack || "",
      componentStack: opts.componentStack || "",
      pluginId,
      surface,
      faultKind: opts.kind,
      faultAction: action,
    });
  } catch {
    /* ignore */
  }
}

/** Install once: attribute window errors from plugin-ui / shell.boot / theme scripts. */
export function installPluginFaultGuards(): () => void {
  if (guardsInstalled || typeof window === "undefined") return () => undefined;
  guardsInstalled = true;

  const onError = (ev: ErrorEvent) => {
    const hit = attributePluginFromErrorEvent(ev);
    if (!hit) return;
    void handlePluginFault({
      pluginId: hit.pluginId,
      surface: hit.surface,
      kind: hit.kind,
      message: ev.message || String(ev.error || "script error"),
      stack: ev.error?.stack || "",
    });
  };

  const onRejection = (ev: PromiseRejectionEvent) => {
    const reason = ev.reason;
    const message =
      reason instanceof Error
        ? reason.message
        : typeof reason === "string"
          ? reason
          : "unhandledrejection";
    const stack = reason instanceof Error ? reason.stack || "" : "";
    // Only act when we can name the plugin — never disable on unknown host bugs.
    const pluginId = attributePluginFromUrl(stack);
    if (!pluginId) return;
    void handlePluginFault({
      pluginId,
      surface: "promise",
      kind: "plugin",
      message,
      stack,
    });
  };

  window.addEventListener("error", onError, true);
  window.addEventListener("unhandledrejection", onRejection);
  return () => {
    window.removeEventListener("error", onError, true);
    window.removeEventListener("unhandledrejection", onRejection);
    guardsInstalled = false;
  };
}
