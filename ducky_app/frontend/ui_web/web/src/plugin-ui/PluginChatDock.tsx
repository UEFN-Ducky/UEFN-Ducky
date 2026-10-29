import { useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import { ChatPane } from "../components/ChatPane";
import { ErrorBoundary } from "../components/ErrorBoundary";
import type { ChatTab } from "../types/panel";
import type { PluginChatOverlayProps } from "./PluginWebviewPane";
import { clampSize, closeDock, getDock, setDock, subscribeDocks, type DockState } from "./pluginChatDockStore";

export function useDock(tabId: string): DockState | undefined {
  return useSyncExternalStore(subscribeDocks, () => getDock(tabId), () => undefined);
}

const clock = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

/** Seconds since the agent started, while it runs (the collapsed line's timer). */
function useRunClock(running: boolean): string {
  const [start, setStart] = useState(0);
  const [, tick] = useState(0);
  useEffect(() => {
    if (!running) return setStart(0);
    setStart(Date.now());
    const id = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [running]);
  return running && start ? clock(Date.now() - start) : "";
}

type Props = {
  tabId: string;
  dock: DockState;
  overlay: PluginChatOverlayProps;
  visible: boolean;
};

/**
 * Ducky's own chat docked inside a plugin pane (bridge `chat.open`): a slim header line
 * (running dot, title, timer, expand / open as tab / close) over the real ChatPane.
 * The plugin restyles it through CSS variables (`theme`), resizes it with `size`.
 */
export function PluginChatDock({ tabId, dock, overlay, visible }: Props) {
  const chat: ChatTab = overlay.allChats.find((c) => c.id === dock.convId) ?? { id: dock.convId, name: dock.title };
  const running = overlay.runningChatIds.has(dock.convId);
  const timer = useRunClock(running);
  const bottom = dock.place === "bottom";
  const drag = useRef<{ start: number; size: number } | null>(null);

  const onResizeDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    drag.current = { start: bottom ? e.clientY : e.clientX, size: dock.size };
  };
  const onResizeMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const delta = (bottom ? e.clientY : e.clientX) - d.start; // the dock grows away from the panel
    setDock(tabId, { size: clampSize(dock.place, d.size - delta) });
  };
  const onResizeUp = () => {
    drag.current = null;
  };

  const style = {
    ...dock.theme,
    ...(dock.expanded ? (bottom ? { height: dock.size } : { width: dock.size }) : {}),
  } as CSSProperties;

  return (
    <section
      className={`plugin-chat-dock plugin-chat-dock--${dock.place}${dock.expanded ? " is-expanded" : ""}`}
      style={style}
      aria-label={`${dock.title} chat`}
    >
      {dock.expanded ? (
        <div
          className="plugin-chat-dock__resize"
          role="separator"
          aria-orientation={bottom ? "horizontal" : "vertical"}
          aria-label="Resize chat"
          onPointerDown={onResizeDown}
          onPointerMove={onResizeMove}
          onPointerUp={onResizeUp}
          onPointerCancel={onResizeUp}
        />
      ) : null}
      <header className="plugin-chat-dock__head">
        <button
          type="button"
          className="plugin-chat-dock__line"
          onClick={() => setDock(tabId, { expanded: !dock.expanded })}
          title={dock.expanded ? "Collapse to one line" : "Show the chat"}
        >
          <span className={`plugin-chat-dock__dot${running ? " is-running" : ""}`} aria-hidden="true" />
          <span className="plugin-chat-dock__title">{running ? "Working" : dock.title}</span>
          {timer ? <span className="plugin-chat-dock__time">{timer}</span> : null}
        </button>
        <button
          type="button"
          className="plugin-chat-dock__btn"
          onClick={() => setDock(tabId, { expanded: !dock.expanded })}
          aria-label={dock.expanded ? "Collapse chat" : "Expand chat"}
          title={dock.expanded ? "Collapse" : "Expand"}
        >
          {dock.expanded ? (bottom ? "▾" : "▸") : bottom ? "▴" : "◂"}
        </button>
        <button
          type="button"
          className="plugin-chat-dock__btn"
          onClick={() => overlay.onOpenChat(chat)}
          aria-label="Open as a chat tab"
          title="Open as a chat tab"
        >
          ↗
        </button>
        <button
          type="button"
          className="plugin-chat-dock__btn"
          onClick={() => closeDock(tabId)}
          aria-label="Close chat"
          title="Close"
        >
          ✕
        </button>
      </header>
      <div className="plugin-chat-dock__body" hidden={!dock.expanded}>
        <ErrorBoundary label={`${dock.title} chat`} resetKeys={[dock.convId]}>
          <ChatPane
            key={dock.convId}
            chat={chat}
            visible={visible && dock.expanded}
            variant="popup"
            allChats={overlay.allChats}
            onOpenChat={overlay.onOpenChat}
            onOpenFile={overlay.onOpenFile}
            onOpenPlan={overlay.onOpenPlan}
            isAgentRunning={running}
            onEngage={overlay.onDismissChatAlert ? () => overlay.onDismissChatAlert?.(dock.convId) : undefined}
          />
        </ErrorBoundary>
      </div>
    </section>
  );
}
