/** Embed a plugin ui.panels entry inside Settings (not an editor tab). */

import { useEffect, useMemo, useRef } from "react";
import "./plugin-ui.css";
import { BRIDGE_CHANNEL, PLUGIN_UI_ROUTE_PREFIX, PLUGIN_UI_SANDBOX } from "./constants";
import { handleBridgeRequest, shouldForwardPluginPush } from "./bridge";
import { usePluginThemePush } from "./pluginTheme";
import { isBridgeRequest } from "./types";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import { PluginSurfaceBoundary } from "./PluginSurfaceBoundary";

type Props = {
  pluginId: string;
  panelId: string;
};

export function PluginSettingsEmbed({ pluginId, panelId }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const contrib = usePluginContributions();
  const pid = pluginId.trim().toLowerCase();
  const panel = useMemo(
    () => contrib.ui_panels.find((p) => p.plugin_id === pid && p.id === panelId),
    [contrib.ui_panels, pid, panelId],
  );

  const src = useMemo(() => {
    if (!panel?.entry) return null;
    const entry = panel.entry.replace(/^\/+/, "");
    return `/${PLUGIN_UI_ROUTE_PREFIX}/${pid}/${entry}`;
  }, [panel?.entry, pid]);

  const tabId = `settings-plugin:${pid}:${panelId}`;

  usePluginThemePush(iframeRef, src);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const iframe = iframeRef.current;
      if (!iframe || event.source !== iframe.contentWindow) return;
      if (!isBridgeRequest(event.data) || event.data.channel !== BRIDGE_CHANNEL) return;
      void handleBridgeRequest(
        {
          pluginId: pid,
          panelId,
          tabId,
          version: panel?.version,
          iframe,
        },
        event.data,
      ).then((response) => {
        iframe.contentWindow?.postMessage(response, "*");
      });
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [pid, panelId, panel?.version, tabId]);

  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      const win = iframeRef.current?.contentWindow;
      if (!win || typeof event.type !== "string") return;
      if (shouldForwardPluginPush(tabId, pid, event.type)) {
        win.postMessage({ channel: BRIDGE_CHANNEL, event }, "*");
      }
    });
  }, [tabId, pid]);

  if (!src) {
    return (
      <div className="general-tab-shell">
        <p className="general-tab-section-desc">Plugin settings panel not found.</p>
      </div>
    );
  }

  return (
    <PluginSurfaceBoundary pluginId={pid} surface={`settings.panel:${panelId}`}>
      <div className="plugin-settings-embed">
        <iframe
          ref={iframeRef}
          className="plugin-ui-iframe"
          title={`${pid} settings`}
          src={src}
          sandbox={PLUGIN_UI_SANDBOX}
          allow="clipboard-read; clipboard-write"
        />
      </div>
    </PluginSurfaceBoundary>
  );
}
