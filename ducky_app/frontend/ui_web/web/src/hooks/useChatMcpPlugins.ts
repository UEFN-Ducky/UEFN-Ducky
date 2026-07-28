import { useCallback, useEffect, useState } from "react";

import { getApi } from "./usePanelApi";
import type { McpPluginDto } from "../types/panel";

/** Per-chat MCP plugin selection: override list, or null = follow global defaults. */
export function useChatMcpPlugins(convId: string, active: boolean) {
  const [plugins, setPlugins] = useState<McpPluginDto[]>([]);
  const [override, setOverride] = useState<string[] | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setLoaded(false);
    setOverride(null);
  }, [convId]);

  useEffect(() => {
    if (!active || loaded) return;
    const api = getApi();
    if (!api?.get_chat_mcp_plugins) return;
    void api.get_chat_mcp_plugins(convId).then((res) => {
      if (!res.ok) return;
      setPlugins(res.plugins ?? []);
      setOverride(res.override ?? null);
      setLoaded(true);
    });
  }, [active, loaded, convId]);

  const effective = override ?? plugins.filter((p) => p.enabled).map((p) => p.id);

  const save = useCallback(
    (ids: string[] | null) => {
      const api = getApi();
      if (!api?.set_chat_mcp_plugins) return;
      // Optimistic — the backend echoes the canonical override back.
      setOverride(ids);
      void api.set_chat_mcp_plugins(convId, ids).then((res) => {
        if (res.ok) setOverride(res.override ?? null);
      });
    },
    [convId],
  );

  const togglePlugin = (pluginId: string, checked: boolean) => {
    const rest = effective.filter((x) => x !== pluginId);
    save(checked ? [...rest, pluginId] : rest);
  };

  return {
    plugins,
    override,
    effective,
    loading: active && !loaded,
    togglePlugin,
    useDefaults: () => save(null),
  };
}
