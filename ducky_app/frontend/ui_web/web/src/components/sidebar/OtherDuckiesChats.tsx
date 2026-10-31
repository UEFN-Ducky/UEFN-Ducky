import { useCallback, useEffect, useRef, useState } from "react";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { getApi } from "../../hooks/usePanelApi";
import { Icons } from "../../icons/Icons";
import { ContextMenu, useContextMenuState } from "../ContextMenu";
import { Modal, ModalActions } from "../Modal";
import { SplitResizeHandle } from "../SplitResizeHandle";
import type { DuckySyncPeer } from "../../types/panel";

const REFRESH_MS = 60_000;
const COLLAPSED_KEY = "ducky.otherDuckies.collapsed";
const HEIGHT_KEY = "ducky.otherDuckies.height";
const MIN_HEIGHT = 60;
const DEFAULT_HEIGHT = 160;

function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage off: it just isn't remembered */
  }
}

/** Body height after dragging the handle above it by ``delta`` px (up = taller). */
export function resizedHeight(start: number, delta: number, max: number): number {
  return Math.round(Math.max(MIN_HEIGHT, Math.min(Math.max(MIN_HEIGHT, max), start - delta)));
}

/** "just now", "5 min ago", "3 h ago", "2 d ago". */
export function agoText(secs: number, now = Date.now() / 1000): string {
  if (!secs) return "never";
  const d = Math.max(0, now - secs);
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return `${Math.floor(d / 86400)} d ago`;
}

/** Peers with chats, live ones first, each with its live flag from the account's list. */
export function withLive(peers: DuckySyncPeer[], live: Record<string, boolean>): Array<DuckySyncPeer & { live: boolean }> {
  return peers
    .filter((p) => p.chats.length)
    .map((p) => ({ ...p, live: !!live[p.ducky] }))
    .sort((a, b) => Number(b.live) - Number(a.live) || a.name.localeCompare(b.name));
}

type CopyView = {
  peer: DuckySyncPeer & { live: boolean };
  chat: DuckySyncPeer["chats"][number];
  rows: Array<{ id?: number | string; role?: string; text?: string }>;
  synced: number;
  error: string;
};

/**
 * Bottom of the Duckies panel: your other UEFN Duckies' chats, copied here by sync. A
 * chat lives on its home Ducky (its agent runs there): pressing one opens it there while
 * that Ducky is online, else shows the copy, read only.
 */
export function OtherDuckiesChats() {
  const [peers, setPeers] = useState<DuckySyncPeer[]>([]);
  const [live, setLive] = useState<Record<string, boolean>>({});
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [copy, setCopy] = useState<CopyView | null>(null);
  const [collapsed, setCollapsed] = useState(() => readStored(COLLAPSED_KEY) === "1");
  const [height, setHeight] = useState(() => Number(readStored(HEIGHT_KEY)) || DEFAULT_HEIGHT);
  const heightRef = useRef(height);
  const dragRef = useRef({ start: height, delta: 0, max: 600 });
  const rootRef = useRef<HTMLDivElement>(null);
  const { confirm, alert } = useConfirmModal();
  const { menu, open: openMenu, close: closeMenu } = useContextMenuState<DuckySyncPeer & { live: boolean }>();

  const load = useCallback(async () => {
    const api = getApi();
    if (!api?.ducky_sync_list) return;
    try {
      const [list, link] = await Promise.all([api.ducky_sync_list(), api.ducky_link_list?.()]);
      if (list?.ok !== false) setPeers(list?.duckies ?? []);
      const flags: Record<string, boolean> = {};
      for (const d of link?.devices ?? []) if (d.keyId) flags[d.keyId] = !!d.live;
      setLive(flags);
    } catch {
      /* signed out or offline: keep what's shown */
    }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, [load]);

  const shown = withLive(peers, live);
  if (!shown.length) return null;
  const anyLive = shown.some((p) => p.live);

  const toggleCollapsed = () => {
    setCollapsed((c) => {
      writeStored(COLLAPSED_KEY, c ? "0" : "1");
      return !c;
    });
  };

  const removePeer = async (peer: DuckySyncPeer & { live: boolean }) => {
    const ok = await confirm({
      title: "Remove UEFN Ducky",
      message: `Remove "${peer.name}"? It is signed out of your account and its chat copies here are deleted. Its own chats stay on that PC; sign in there again to bring it back.`,
      confirmLabel: "Remove",
      danger: true,
    });
    if (ok !== true) return;
    const res = await getApi()?.ducky_sync_forget?.(peer.ducky);
    if (res?.ok === false) {
      await alert({ title: "Couldn't remove it", message: res.error || "Try again in a moment." });
      return;
    }
    setPeers((list) => list.filter((p) => p.ducky !== peer.ducky));
    if (res?.revoke_error) {
      await alert({
        title: "Removed here",
        message: `Its copies are gone, but signing it out failed: ${res.revoke_error}`,
      });
    }
  };

  const openChat = async (peer: DuckySyncPeer & { live: boolean }, chat: DuckySyncPeer["chats"][number]) => {
    const api = getApi();
    if (peer.live && api?.ducky_link_open) {
      void api.ducky_link_open(peer.ducky, peer.name, chat.conv_id);
      return;
    }
    const res = await api?.ducky_sync_copy?.(peer.ducky, chat.conv_id);
    setCopy({
      peer,
      chat,
      rows: res?.rows ?? [],
      synced: Number(res?.synced || chat.synced || 0),
      error: res?.ok === false ? String(res.error || "") : "",
    });
  };

  return (
    <div ref={rootRef} className={`other-duckies${collapsed ? " is-collapsed" : ""}`} aria-label="Chats on your other UEFN Duckies">
      {!collapsed ? (
        <SplitResizeHandle
          className="other-duckies__split"
          orientation="vertical"
          ariaLabel="Resize Duckies and your other Duckies"
          onDragStart={() => {
            // Leave the Duckies list at least its minimum above.
            const panel = rootRef.current?.parentElement?.getBoundingClientRect().height ?? 600;
            dragRef.current = { start: heightRef.current, delta: 0, max: panel - 120 };
          }}
          onDrag={(delta) => {
            const d = dragRef.current;
            d.delta += delta;
            const next = resizedHeight(d.start, d.delta, d.max);
            heightRef.current = next;
            setHeight(next);
          }}
          onDragEnd={() => writeStored(HEIGHT_KEY, String(heightRef.current))}
        />
      ) : null}
      <div
        className="other-duckies__head"
        role="button"
        tabIndex={0}
        aria-expanded={!collapsed}
        title={collapsed ? "Show your other Duckies" : "Hide your other Duckies"}
        onClick={toggleCollapsed}
        onKeyDown={(e) => {
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          toggleCollapsed();
        }}
      >
        <span className={`other-duckies__chev${collapsed ? "" : " is-open"}`} aria-hidden="true">
          <Icons.ChevronRight />
        </span>
        <span
          className={`other-duckies__dot${anyLive ? " is-live" : ""}`}
          title={anyLive ? "One or more is online" : "All offline"}
          aria-hidden="true"
        />
        <span className="other-duckies__title">On your other Duckies</span>
        <span className="other-duckies__count">{shown.length}</span>
        <button
          type="button"
          className="other-duckies__sync"
          title="Sync now"
          aria-label="Sync now with your other UEFN Duckies"
          onClick={(e) => {
            e.stopPropagation();
            void getApi()?.ducky_sync_now?.();
            window.setTimeout(() => void load(), 8_000);
          }}
        >
          <Icons.Refresh />
        </button>
      </div>
      <div className={`other-duckies__clip${collapsed ? "" : " is-open"}`} aria-hidden={collapsed || undefined}>
      <div className="other-duckies__clip-inner">
      <div className="other-duckies__body" style={{ height }}>
      {shown.map((peer) => {
        const expanded = !!open[peer.ducky];
        return (
          <div key={peer.ducky} className="other-duckies__peer">
            <button
              type="button"
              className="other-duckies__peer-row"
              aria-expanded={expanded}
              title="Right-click to remove"
              onClick={() => setOpen((o) => ({ ...o, [peer.ducky]: !expanded }))}
              onContextMenu={(e) => openMenu(e, peer)}
            >
              <span className={`other-duckies__chev${expanded ? " is-open" : ""}`} aria-hidden="true">
                <Icons.ChevronRight />
              </span>
              <span className={`other-duckies__dot${peer.live ? " is-live" : ""}`} aria-hidden="true" />
              <span className="other-duckies__name">{peer.name}</span>
              <span className="other-duckies__meta">{peer.live ? "online" : `synced ${agoText(peer.synced)}`}</span>
            </button>
            <div className={`other-duckies__clip${expanded ? " is-open" : ""}`} aria-hidden={!expanded || undefined}>
              <div className="other-duckies__clip-inner">
                {peer.chats.map((chat) => (
                  <button
                    key={chat.conv_id}
                    type="button"
                    className="other-duckies__chat"
                    title={peer.live ? `Open on ${peer.name}` : `Read the copy from ${agoText(chat.synced)}`}
                    onClick={() => void openChat(peer, chat)}
                  >
                    <Icons.Chat />
                    <span className="other-duckies__chat-title">{chat.title || "Untitled chat"}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        );
      })}
      </div>
      </div>
      </div>
      {menu ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          onClose={closeMenu}
          items={[
            {
              id: "open",
              label: menu.data.live ? `Open ${menu.data.name}` : `${menu.data.name} is offline`,
              disabled: !menu.data.live,
              onClick: () => void getApi()?.ducky_link_open?.(menu.data.ducky, menu.data.name, ""),
            },
            { id: "remove", label: "Remove (disconnect)", danger: true, onClick: () => void removePeer(menu.data) },
          ]}
        />
      ) : null}
      <Modal
        open={!!copy}
        onClose={() => setCopy(null)}
        title={copy ? copy.chat.title || "Untitled chat" : ""}
        width={640}
        footer={
          copy ? (
            <ModalActions
              onCancel={() => setCopy(null)}
              cancelLabel="Close"
              confirmLabel={`Open on ${copy.peer.name}`}
              confirmDisabled={!copy.peer.live}
              onConfirm={() => {
                void getApi()?.ducky_link_open?.(copy.peer.ducky, copy.peer.name, copy.chat.conv_id);
                setCopy(null);
              }}
            />
          ) : null
        }
      >
        {copy ? (
          <div className="other-duckies-copy">
            <p className="other-duckies-copy__note">
              On {copy.peer.name} · a read-only copy from {agoText(copy.synced)}.
              {copy.peer.live ? "" : ` ${copy.peer.name} is offline: open it there to talk to it.`}
            </p>
            {copy.error ? <p className="other-duckies-copy__note">{copy.error}</p> : null}
            {copy.rows.map((row, i) => (
              <div key={`${row.id ?? i}-${i}`} className={`other-duckies-copy__row is-${row.role || "assistant"}`}>
                <span className="other-duckies-copy__who">{row.role === "user" ? "You" : row.role === "tool" ? "Tool" : "Ducky"}</span>
                <span className="other-duckies-copy__text">{row.text}</span>
              </div>
            ))}
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
