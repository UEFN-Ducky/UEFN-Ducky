/**
 * Sandboxed plugin iframe bound to a project file path.
 *
 * Used when a Store plugin contributes ``editor.kinds`` claiming a file type
 * (e.g. ``unreal_asset`` / ``model``). The path is exposed via ``plugin.info``.
 */

import { useEffect, useMemo, useRef } from "react";
import "./plugin-ui.css";
import {
  PLUGIN_UI_ROUTE_PREFIX,
  PLUGIN_UI_SANDBOX,
  BRIDGE_CHANNEL,
} from "./constants";
import {
  handleBridgeRequest,
  shouldForwardPluginPush,
} from "./bridge";
import { usePluginThemePush } from "./pluginTheme";
import { isBridgeRequest, type PluginUiPanel } from "./types";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import { PluginSurfaceBoundary } from "./PluginSurfaceBoundary";

type Props = {
  pluginId: string;
  panelId: string;
  relativePath: string;
};

function findPanel(
  panels: PluginUiPanel[],
  pluginId: string,
  panelId: string,
): PluginUiPanel | undefined {
  return panels.find((p) => p.plugin_id === pluginId && p.id === panelId);
}

export function PluginFilePane({ pluginId, panelId, relativePath }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const contrib = usePluginContributions();
  const pid = pluginId.trim().toLowerCase();
  const pan = panelId.trim().toLowerCase();
  const tabId = `plugin-file:${pid}:${pan}:${relativePath}`;

  const panel = useMemo(
    () => findPanel(contrib.ui_panels, pid, pan),
    [contrib.ui_panels, pid, pan],
  );

  const src = useMemo(() => {
    if (!panel?.entry) return null;
    const entry = panel.entry.replace(/^\/+/, "");
    const q = new URLSearchParams({ file: relativePath });
    return `/${PLUGIN_UI_ROUTE_PREFIX}/${pid}/${entry}?${q.toString()}`;
  }, [panel, pid, relativePath]);

  usePluginThemePush(iframeRef, src);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const iframe = iframeRef.current;
      if (!iframe || event.source !== iframe.contentWindow) return;
      if (!isBridgeRequest(event.data) || event.data.channel !== BRIDGE_CHANNEL) return;
      void handleBridgeRequest(
        {
          pluginId: pid,
          panelId: pan,
          tabId,
          version: panel?.version,
          filePath: relativePath,
          iframe,
        },
        event.data,
      ).then((response) => {
        iframe.contentWindow?.postMessage(response, "*");
      });
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [pid, pan, panel?.version, tabId, relativePath]);

  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      const win = iframeRef.current?.contentWindow;
      if (!win) return;
      if (
        typeof event.type === "string" &&
        shouldForwardPluginPush(tabId, pid, event.type)
      ) {
        win.postMessage({ channel: BRIDGE_CHANNEL, event }, "*");
      }
    });
  }, [tabId, pid]);

  if (!panel || !src) {
    return (
      <div className="plugin-ui-pane plugin-ui-pane--empty">
        Plugin UI unavailable. Is the plugin still installed and enabled?
      </div>
    );
  }

  return (
    <PluginSurfaceBoundary pluginId={pid} surface={`editor.file:${pan}`}>
      <div className="plugin-ui-pane">
        <iframe
          ref={iframeRef}
          className="plugin-ui-iframe"
          title={panel.title || panel.id}
          src={src}
          sandbox={PLUGIN_UI_SANDBOX}
          allow="pointer-lock"
        />
      </div>
    </PluginSurfaceBoundary>
  );
}
