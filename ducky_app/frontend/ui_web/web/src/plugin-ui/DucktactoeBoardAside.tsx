import { useEffect, useMemo, useRef, useState } from "react";
import "./plugin-ui.css";
import { PLUGIN_UI_ROUTE_PREFIX, PLUGIN_UI_SANDBOX, BRIDGE_CHANNEL } from "./constants";
import { handleBridgeRequest } from "./bridge";
import { isBridgeRequest } from "./types";
import { Icons } from "../icons/Icons";
import {
  DUCKTACTOE_BOARD_PANEL_ID,
  DUCKTACTOE_PLUGIN_ID,
} from "./ducktactoeBoardChat";

type Props = {
  /** Stable id for bridge context (chat id or plugin tab id). */
  hostId: string;
  collapsed: boolean;
  onToggle: () => void;
  /** Pixel width when expanded (resizable from the chat shell). */
  widthPx?: number;
};

/**
 * Collapsible Duck-Tac-Toe board iframe for embedding inside a chat shell.
 */
export function DucktactoeBoardAside({ hostId, collapsed, onToggle, widthPx }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [missing, setMissing] = useState(false);

  const src = useMemo(
    () => `/${PLUGIN_UI_ROUTE_PREFIX}/${DUCKTACTOE_PLUGIN_ID}/ui/index.html`,
    [],
  );

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const iframe = iframeRef.current;
      if (!iframe || event.source !== iframe.contentWindow) return;
      if (!isBridgeRequest(event.data) || event.data.channel !== BRIDGE_CHANNEL) return;
      void handleBridgeRequest(
        {
          pluginId: DUCKTACTOE_PLUGIN_ID,
          panelId: DUCKTACTOE_BOARD_PANEL_ID,
          tabId: `ducktactoe-aside:${hostId}`,
          iframe,
        },
        event.data,
      ).then((response) => {
        iframe.contentWindow?.postMessage(response, "*");
      });
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [hostId]);

  useEffect(() => {
    // Probe once — plugin may be disabled / not installed.
    // Use GET: panel_httpd has no do_HEAD → HEAD always 501 (false "unavailable").
    let cancelled = false;
    void fetch(src, { method: "GET", cache: "no-store" })
      .then((r) => {
        if (!cancelled) setMissing(!r.ok);
      })
      .catch(() => {
        if (!cancelled) setMissing(true);
      });
    return () => {
      cancelled = true;
    };
  }, [src]);

  if (collapsed) {
    return (
      <button
        type="button"
        className="ducktactoe-board-aside ducktactoe-board-aside--collapsed"
        title="Show board"
        onClick={onToggle}
      >
        <Icons.Maximize />
        <span>Board</span>
      </button>
    );
  }

  return (
    <aside
      className="ducktactoe-board-aside"
      aria-label="Duck-Tac-Toe board"
      style={
        widthPx && widthPx > 0
          ? { flex: `0 0 ${widthPx}px`, width: widthPx, maxWidth: "none" }
          : undefined
      }
    >
      <div className="ducktactoe-board-aside__chrome">
        <span className="ducktactoe-board-aside__title">Board</span>
        <button
          type="button"
          className="ducktactoe-board-aside__btn"
          title="Hide board"
          onClick={onToggle}
        >
          <Icons.Minimize />
        </button>
      </div>
      {missing ? (
        <div className="ducktactoe-board-aside__missing">
          Duck-Tac-Toe plugin unavailable. Enable it in Settings → Store.
        </div>
      ) : (
        <iframe
          ref={iframeRef}
          className="plugin-ui-iframe ducktactoe-board-aside__iframe"
          title="Duck-Tac-Toe board"
          src={src}
          sandbox={PLUGIN_UI_SANDBOX}
        />
      )}
    </aside>
  );
}
