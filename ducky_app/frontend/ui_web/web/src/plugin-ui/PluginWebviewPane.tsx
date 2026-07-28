import { useEffect, useMemo, useRef } from "react";
import "./plugin-ui.css";
import {
  PLUGIN_UI_ROUTE_PREFIX,
  PLUGIN_UI_SANDBOX,
  BRIDGE_CHANNEL,
} from "./constants";
import {
  beginBrowserPaneMount,
  handleBridgeRequest,
  hideBrowserPaneOnUnmount,
  pushBrowserPaneBounds,
} from "./bridge";
import { isBridgeRequest, parsePluginUiTabId, type PluginUiPanel } from "./types";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import type { ChatTab } from "../types/panel";
import { DucktactoeChatPopup } from "./DucktactoeChatPopup";
import { isDucktactoeBoardTab } from "./ducktactoeBoardChat";
import { PluginSurfaceBoundary } from "./PluginSurfaceBoundary";

export type PluginChatOverlayProps = {
  allChats: ChatTab[];
  runningChatIds: Set<string>;
  onOpenChat: (chat: ChatTab) => void;
  onOpenFile?: (path: string, name: string, options?: { line?: number }) => void;
  onOpenPlan?: (chatId: string, title?: string) => void;
  onDismissChatAlert?: (chatId: string) => void;
};

type Props = {
  /** Editor tab id: `plugin:<pluginId>:<panelId>`. */
  tabId: string;
  /** Host chat overlay (Duck-Tac-Toe board). */
  chatOverlay?: PluginChatOverlayProps;
};

function findPanel(
  panels: PluginUiPanel[],
  pluginId: string,
  panelId: string,
): PluginUiPanel | undefined {
  return panels.find((p) => p.plugin_id === pluginId && p.id === panelId);
}

/** Sandboxed iframe hosting a plugin's contributes.ui.panels entry. */
export function PluginWebviewPane({ tabId, chatOverlay }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const contrib = usePluginContributions();
  const parsed = useMemo(() => parsePluginUiTabId(tabId), [tabId]);

  const panel = useMemo(() => {
    if (!parsed) return undefined;
    return findPanel(contrib.ui_panels, parsed.pluginId, parsed.panelId);
  }, [contrib.ui_panels, parsed]);

  const src = useMemo(() => {
    if (!parsed || !panel?.entry) return null;
    const entry = panel.entry.replace(/^\/+/, "");
    // Same-origin relative URL so Vite proxy / panel_httpd both work.
    return `/${PLUGIN_UI_ROUTE_PREFIX}/${parsed.pluginId}/${entry}`;
  }, [parsed, panel]);

  const showDucktactoeChat =
    !!chatOverlay &&
    !!parsed &&
    isDucktactoeBoardTab(parsed.pluginId, parsed.panelId);

  useEffect(() => {
    if (!parsed) return;
    const onMessage = (event: MessageEvent) => {
      const iframe = iframeRef.current;
      if (!iframe || event.source !== iframe.contentWindow) return;
      if (!isBridgeRequest(event.data) || event.data.channel !== BRIDGE_CHANNEL) return;
      void handleBridgeRequest(
        {
          pluginId: parsed.pluginId,
          panelId: parsed.panelId,
          tabId,
          version: panel?.version,
          iframe,
        },
        event.data,
      ).then((response) => {
        // Opaque-origin iframes can't be named — data is plugin-scoped prefs only.
        iframe.contentWindow?.postMessage(response, "*");
      });
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [parsed, panel?.version, tabId]);

  // Native browser pane plumbing (bridge `browser.*`): forward navigation-state
  // pushes into the sandbox, and hide the pane while this tab is unmounted (tab
  // switch). The native control is destroyed only when the tab CLOSES.
  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type !== "browser_pane_state" || event.pane_id !== tabId) return;
      iframeRef.current?.contentWindow?.postMessage(
        { channel: BRIDGE_CHANNEL, event },
        "*",
      );
    });
  }, [tabId]);

  // Re-pin the native pane every frame from the live iframe rect (deduped inside
  // pushBrowserPaneBounds) — window resizes / sidebar toggles / split drags track
  // smoothly without waiting on the plugin's own (bridge round-trip) reports.
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      pushBrowserPaneBounds(tabId, iframeRef.current);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [tabId]);

  useEffect(() => {
    beginBrowserPaneMount(tabId);
    return () => {
      // Tab switch: hide the native pane but KEEP its inset. A remount bumps
      // mount gen so a late hide cannot leave the pane stuck blank/brown.
      // Full cleanup happens on tab close (ChatView → browser_pane_close).
      hideBrowserPaneOnUnmount(tabId);
    };
  }, [tabId]);

  if (!parsed) {
    return <div className="plugin-ui-pane plugin-ui-pane--empty">Invalid plugin tab.</div>;
  }
  if (!panel || !src) {
    return (
      <div className="plugin-ui-pane plugin-ui-pane--empty">
        Plugin UI unavailable. Is the plugin still installed and enabled?
      </div>
    );
  }

  // Duck-Tac-Toe is chat-first — do not keep a bare board iframe as the primary surface.
  if (showDucktactoeChat && chatOverlay) {
    return (
      <PluginSurfaceBoundary pluginId={parsed.pluginId} surface={`ui.panel:${parsed.panelId}`}>
        <DucktactoeChatPopup tabId={tabId} {...chatOverlay} />
      </PluginSurfaceBoundary>
    );
  }

  return (
    <PluginSurfaceBoundary pluginId={parsed.pluginId} surface={`ui.panel:${parsed.panelId}`}>
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
