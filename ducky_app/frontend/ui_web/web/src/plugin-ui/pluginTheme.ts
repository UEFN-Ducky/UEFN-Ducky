/**
 * Appearance CSS vars for sandboxed plugin iframes.
 *
 * Opaque-origin iframes cannot inherit :root from the host, so ThemeProvider
 * snapshots cssVars here; panes push them via postMessage and plugins may pull
 * with bridge method ``theme.get``.
 */

import { useEffect, type RefObject } from "react";
import { BRIDGE_CHANNEL } from "./constants";

export const PLUGIN_THEME_EVENT = "ducky-plugin-theme";

export type PluginThemeDetail = { vars: Record<string, string> };

let latestVars: Record<string, string> = {};

export function getPluginThemeVars(): Record<string, string> {
  return { ...latestVars };
}

/** Called from ThemeProvider whenever Appearance cssVars change. */
export function setPluginThemeVars(vars: Record<string, string>): void {
  latestVars = vars && typeof vars === "object" ? { ...vars } : {};
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<PluginThemeDetail>(PLUGIN_THEME_EVENT, {
      detail: { vars: getPluginThemeVars() },
    }),
  );
}

export function postAppearanceTheme(
  win: Window | null | undefined,
  vars: Record<string, string> = getPluginThemeVars(),
): void {
  if (!win) return;
  win.postMessage(
    {
      channel: BRIDGE_CHANNEL,
      event: { type: "appearance_theme", vars },
    },
    "*",
  );
}

/** Keep a plugin iframe's CSS vars in sync with Appearance (load + live updates). */
export function usePluginThemePush(
  iframeRef: RefObject<HTMLIFrameElement | null>,
  /** Remount when src changes so load listener rebinds. */
  src: string | null,
): void {
  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe || !src) return;

    const push = () => postAppearanceTheme(iframe.contentWindow);

    iframe.addEventListener("load", push);
    window.addEventListener(PLUGIN_THEME_EVENT, push);
    push();
    return () => {
      iframe.removeEventListener("load", push);
      window.removeEventListener(PLUGIN_THEME_EVENT, push);
    };
  }, [iframeRef, src]);
}
