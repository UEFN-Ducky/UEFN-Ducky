/** Inject appearance.css only for the active plugin theme/skin — never shared across profiles. */

import { useEffect, useMemo, useRef } from "react";
import { usePluginContributions, type PluginAppearanceCss } from "../hooks/usePluginContributions";
import { PLUGIN_UI_ROUTE_PREFIX } from "../plugin-ui/constants";
import { useAppearance } from "./AppearanceContext";
import {
  isPluginProfileId,
  parsePluginProfileId,
  parsePluginSkinId,
} from "./appearancePluginIds";
import { handlePluginFault } from "../plugin-ui/pluginCrashGuard";

function cssUrl(row: PluginAppearanceCss): string {
  const entry = row.entry.replace(/^\/+/, "");
  return `/${PLUGIN_UI_ROUTE_PREFIX}/${row.plugin_id}/${entry}?t=${Date.now()}`;
}

export function AppearanceCssBridge() {
  const contrib = usePluginContributions();
  const { skinId, activeProfileId, appearanceReady } = useAppearance();
  const loaded = useRef(new Map<string, HTMLLinkElement>());

  const allowedPlugins = useMemo(() => {
    const ids = new Set<string>();
    const skin = parsePluginSkinId((skinId || "").trim());
    if (skin) ids.add(skin.pluginId);
    if (isPluginProfileId(activeProfileId)) {
      const profile = parsePluginProfileId(activeProfileId);
      if (profile) ids.add(profile.pluginId);
    }
    return ids;
  }, [skinId, activeProfileId]);

  useEffect(() => {
    if (!appearanceReady) return;

    const rows = contrib.appearance_css.filter((c) => allowedPlugins.has(c.plugin_id));
    const wanted = new Set(rows.map((c) => `${c.plugin_id}::${c.entry}`));

    for (const [key, el] of [...loaded.current.entries()]) {
      if (!wanted.has(key)) {
        el.remove();
        loaded.current.delete(key);
      }
    }

    for (const row of rows) {
      const key = `${row.plugin_id}::${row.entry}`;
      if (loaded.current.has(key)) continue;
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = cssUrl(row);
      link.dataset.uefnAppearanceCss = key;
      link.onerror = () => {
        console.warn("[uefn appearance.css] failed to load", row.plugin_id, row.entry);
        void handlePluginFault({
          pluginId: row.plugin_id,
          surface: "css",
          kind: "theme",
          message: `Failed to load appearance CSS: ${row.entry}`,
        });
      };
      document.head.appendChild(link);
      loaded.current.set(key, link);
    }
  }, [appearanceReady, contrib.appearance_css, contrib.enabled_ids, allowedPlugins]);

  return null;
}
