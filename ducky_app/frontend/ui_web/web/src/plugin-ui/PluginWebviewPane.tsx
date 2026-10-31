import { useEffect, useMemo, useRef } from "react";
import "./plugin-ui.css";
import {
  PLUGIN_UI_SANDBOX,
  BRIDGE_CHANNEL,
} from "./constants";
import {
  beginBrowserPaneMount,
  handleBridgeRequest,
  hideBrowserPaneOnUnmount,
  ownsBrowserPane,
  pushOwnBrowserPanes,
  shouldForwardPluginPush,
} from "./bridge";
import { usePluginThemePush } from "./pluginTheme";
import { reloadIframe, useReloadOnPluginUpdate } from "./pluginReload";
import { isBridgeRequest, parsePluginUiTabId, type PluginUiPanel } from "./types";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import type { ChatTab } from "../types/panel";
import { DucktactoeChatPopup } from "./DucktactoeChatPopup";
import { isDucktactoeBoardTab } from "./ducktactoeBoardChat";
import { PluginSurfaceBoundary } from "./PluginSurfaceBoundary";
import { PluginScopeBar } from "./PluginScopeBar";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { usePluginPage } from "./usePluginPage";
import { WINDOW_ID } from "../tabs/tabRegistryClient";
import { PluginChatDock, useDock } from "./PluginChatDock";
import { dockSnapshot, dropDock, registerChatLookup } from "./pluginChatDockStore";
import { panelItemKey, subscribePanelItems, takePanelItem } from "./panelOpenItem";

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
  /** False while the tab is kept mounted but hidden (another tab of its group is active). */
  visible?: boolean;
};

/** Bridge event `panel.visibility`: the panel's tab was shown or hidden. */
function postVisibility(iframe: HTMLIFrameElement | null, visible: boolean): void {
  iframe?.contentWindow?.postMessage({ channel: BRIDGE_CHANNEL, event: { type: "panel.visibility", visible } }, "*");
}

/** Bridge event `live.state`: the plugin went live on its team copy or left (the host's
 * live script in the page starts or stops). */
function postLive(iframe: HTMLIFrameElement | null, on: boolean): void {
  iframe?.contentWindow?.postMessage({ channel: BRIDGE_CHANNEL, event: { type: "live.state", on } }, "*");
}

type SyncState = { shared: boolean; incoming: number };

/** Bridge event `sync.state`: a team copy or not, and teammates' changes on their way (the
 * live script's placeholder for a picture that isn't here yet says "Getting picture…"). */
function postSync(iframe: HTMLIFrameElement | null, state: SyncState): void {
  iframe?.contentWindow?.postMessage({ channel: BRIDGE_CHANNEL, event: { type: "sync.state", ...state } }, "*");
}

type Follow = { device: string; leading: boolean; older: string[] };

/** Bridge event `live.follow`: the group's leader whose screen this page follows (their
 * device), or "" for nobody; `leading` on the leader's own page; `older`: groupmates on an
 * older app, whose clicks the leader does for everyone. */
function postFollow(iframe: HTMLIFrameElement | null, follow: Follow): void {
  iframe?.contentWindow?.postMessage({ channel: BRIDGE_CHANNEL, event: { type: "live.follow", ...follow } }, "*");
}

/** Bridge event `panel.open_item`: a chat or search reference picked this item of the panel. */
function postOpenItem(iframe: HTMLIFrameElement | null, item: string | null): void {
  if (!item) return;
  iframe?.contentWindow?.postMessage({ channel: BRIDGE_CHANNEL, event: { type: "panel.open_item", item } }, "*");
}

function findPanel(
  panels: PluginUiPanel[],
  pluginId: string,
  panelId: string,
): PluginUiPanel | undefined {
  return panels.find((p) => p.plugin_id === pluginId && p.id === panelId);
}

/** Sandboxed iframe hosting a plugin's contributes.ui.panels entry. */
export function PluginWebviewPane({ tabId, chatOverlay, visible = true }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const contrib = usePluginContributions();
  const parsed = useMemo(() => parsePluginUiTabId(tabId), [tabId]);

  const panel = useMemo(() => {
    if (!parsed) return undefined;
    return findPanel(contrib.ui_panels, parsed.pluginId, parsed.panelId);
  }, [contrib.ui_panels, parsed]);

  const page = usePluginPage(parsed?.pluginId ?? "", panel?.entry, panel?.version);
  const src = page.src;

  // Ducky's own chat docked in this pane (bridge `chat.open`); the pane needs the window's chats to show it.
  const dock = useDock(tabId);
  const dockShown = !!dock?.open && !!dock.convId && !!chatOverlay;
  const dockRunning = !!dock && !!chatOverlay?.runningChatIds.has(dock.convId);
  const allChats = chatOverlay?.allChats;
  useEffect(() => {
    if (!allChats) return;
    // A remembered dock chat that was deleted is replaced (empty list = not loaded yet: trust it).
    registerChatLookup((id) => allChats.length === 0 || allChats.some((c) => c.id === id));
  }, [allChats]);
  useEffect(() => {
    iframeRef.current?.contentWindow?.postMessage(
      { channel: BRIDGE_CHANNEL, event: { type: "chat.state", ...dockSnapshot(dock, dockRunning) } },
      "*",
    );
  }, [dock, dockRunning]);
  useEffect(() => () => dropDock(tabId), [tabId]);

  const showDucktactoeChat =
    !!chatOverlay &&
    !!parsed &&
    isDucktactoeBoardTab(parsed.pluginId, parsed.panelId);

  usePluginThemePush(iframeRef, src);
  useReloadOnPluginUpdate(iframeRef, panel?.version);

  // Data switched between Local and a shared space: the plugin restarted on the other copy,
  // so the panel loads again too (nothing of the old copy stays on screen).
  const pluginId = parsed?.pluginId ?? "";
  useEffect(() => {
    if (!pluginId) return;
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type !== "plugin_scope_changed" || !event.switched || !event.plugins?.includes(pluginId)) return;
      reloadIframe(iframeRef.current);
    });
  }, [pluginId]);

  // A hidden tab stays mounted, so the panel is told when it is shown or hidden
  // (bridge event `panel.visibility`) and can pause heavy work: on every change,
  // and on each load (a reload starts the plugin over).
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  const liveRef = useRef(false);
  const syncRef = useRef<SyncState>({ shared: false, incoming: 0 });
  const followRef = useRef<Follow>({ device: "", leading: false, older: [] });
  const itemKey = parsed ? panelItemKey(parsed.pluginId, parsed.panelId) : "";
  const loadedRef = useRef(false);
  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe || !src) return;
    loadedRef.current = false;
    const post = () => {
      loadedRef.current = true;
      postVisibility(iframe, visibleRef.current);
      postLive(iframe, liveRef.current);
      postSync(iframe, syncRef.current);
      postFollow(iframe, followRef.current);
      postOpenItem(iframe, takePanelItem(itemKey));
    };
    iframe.addEventListener("load", post);
    return () => iframe.removeEventListener("load", post);
  }, [src, itemKey]);
  // A reference to an item (a Kanban board) while this panel is already open.
  useEffect(() => subscribePanelItems((key) => {
    if (key === itemKey && loadedRef.current) postOpenItem(iframeRef.current, takePanelItem(itemKey));
  }), [itemKey]);
  useEffect(() => {
    postVisibility(iframeRef.current, visible);
  }, [visible]);
  // Ducky knows which plugin panels are on screen, so an agent testing one is told
  // when its panel code is paused (a hidden tab) instead of reading stale results.
  useEffect(() => {
    void getApi()?.report_plugin_panel?.(WINDOW_ID, tabId, visible, true);
  }, [tabId, visible]);
  useEffect(() => () => void getApi()?.report_plugin_panel?.(WINDOW_ID, tabId, false, false), [tabId]);

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
  // Also forward plugin.subscribe event types (e.g. discord_message).
  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      const win = iframeRef.current?.contentWindow;
      if (!win) return;
      // Its own panes' state, popups and tab commands (page tabs are `<tab>::<page tab>`).
      if (
        (event.type === "browser_pane_state" || event.type === "browser_pane_new_window"
          || event.type === "browser_tab_command")
        && typeof event.pane_id === "string" && ownsBrowserPane(tabId, event.pane_id)
      ) {
        win.postMessage({ channel: BRIDGE_CHANNEL, event }, "*");
        return;
      }
      // Live editing: a teammate's message, who is live, what teammates hold, a groupmate's
      // live state or one of the group's shared values, for this plugin's panels only.
      if ((event.type === "live_message" || event.type === "live_peers" || event.type === "live_holds"
          || event.type === "live_member_state" || event.type === "live_shared")
          && parsed && event.plugin === parsed.pluginId) {
        const e = event as { from?: unknown; state?: unknown; holds?: unknown; key?: unknown; value?: unknown; by?: unknown };
        win.postMessage({
          channel: BRIDGE_CHANNEL,
          event: event.type === "live_message"
            ? { type: "live.message", from: e.from, topic: event.topic, data: event.data }
            : event.type === "live_member_state"
              ? { type: "live.memberState", from: e.from, state: e.state ?? null }
              : event.type === "live_shared"
                ? { type: "live.shared", key: e.key, value: e.value ?? null, by: e.by }
                : event.type === "live_holds"
                  ? { type: "live.holds", holds: e.holds ?? [] }
                  : { type: "live.peers", on: Boolean(event.on), peers: event.peers ?? [] },
        }, "*");
        return;
      }
      if (
        parsed &&
        typeof event.type === "string" &&
        shouldForwardPluginPush(tabId, parsed.pluginId, event.type)
      ) {
        win.postMessage({ channel: BRIDGE_CHANNEL, event }, "*");
      }
    });
  }, [tabId, parsed]);

  // Re-pin the native pane every frame from the live iframe rect (deduped inside
  // pushBrowserPaneBounds) — window resizes / sidebar toggles / split drags track
  // smoothly without waiting on the plugin's own (bridge round-trip) reports.
  // Native WebView2 panes are desktop-only (REMOTE_DENY); skip the rAF on stream.
  // A hidden tab has no rect to pin to: no rAF until it shows again.
  useEffect(() => {
    if (isRemote()) return;
    if (!visible) return;
    let raf = 0;
    const tick = () => {
      pushOwnBrowserPanes(tabId, iframeRef.current);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [tabId, visible]);

  useEffect(() => {
    if (!visible) return;
    beginBrowserPaneMount(tabId);
    return () => {
      // Tab switch (hidden or unmounted): hide the native pane but KEEP its inset,
      // so it never floats over another tab. A remount bumps mount gen so a late
      // hide cannot leave the pane stuck blank/brown.
      // Full cleanup happens on tab close (ChatView → browser_pane_close).
      hideBrowserPaneOnUnmount(tabId);
    };
  }, [tabId, visible]);

  if (!parsed) {
    return <div className="plugin-ui-pane plugin-ui-pane--empty">Invalid plugin tab.</div>;
  }
  if (panel && page.linked && !src) {
    return (
      <div className="plugin-ui-pane plugin-ui-pane--empty">
        {page.error ? `Couldn't open this page from your PC: ${page.error}` : "Opening from your PC…"}
      </div>
    );
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
        <PluginScopeBar
          pluginId={parsed.pluginId}
          active={visible}
          onLiveState={(on) => {
            if (liveRef.current === on) return;
            liveRef.current = on;
            postLive(iframeRef.current, on);
          }}
          onSyncState={(state) => {
            syncRef.current = state;
            postSync(iframeRef.current, state);
          }}
          onFollow={(device, leading, older) => {
            followRef.current = { device, leading, older };
            postFollow(iframeRef.current, followRef.current);
          }}
          onScopeChanged={(status, change) =>
            iframeRef.current?.contentWindow?.postMessage(
              { channel: BRIDGE_CHANNEL, event: { type: "plugin_scope_changed", scope: status.scope, ...change } },
              "*",
            )
          }
        />
        <div className={`plugin-ui-body${dockShown && dock ? ` plugin-ui-body--dock-${dock.place}` : ""}`}>
          <iframe
            ref={iframeRef}
            className="plugin-ui-iframe"
            title={panel.title || panel.id}
            src={src}
            sandbox={PLUGIN_UI_SANDBOX}
          />
          {dockShown && dock && chatOverlay ? (
            <PluginChatDock tabId={tabId} dock={dock} overlay={chatOverlay} visible={visible} />
          ) : null}
        </div>
      </div>
    </PluginSurfaceBoundary>
  );
}
