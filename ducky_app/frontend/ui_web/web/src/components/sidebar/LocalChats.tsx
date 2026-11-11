import { useCallback, useEffect, useRef, useState } from "react";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { useChatsChanged } from "../../hooks/useChatsChanged";
import { getApi } from "../../hooks/usePanelApi";
import { subscribePanelPush } from "../../hooks/usePanelPushBus";
import { Icons } from "../../icons/Icons";
import { ContextMenu, useContextMenuState } from "../ContextMenu";
import type { LocalChatRow } from "../../types/panel";
import { agoText } from "./OtherDuckiesChats";

const COLLAPSED_KEY = "ducky.localChats.collapsed";
const ALL = "*";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

function writeCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    /* storage off: it just isn't remembered */
  }
}

/** "3 chats" for a group, else how long ago the chat was last used. */
export function localChatMeta(row: LocalChatRow, now = Date.now() / 1000): string {
  if (row.group || row.chats > 1) return `${row.chats} chats`;
  return row.updated ? agoText(row.updated, now) : "";
}

/**
 * Under the Duckies list: the chats made on this PC before signing in. They belong to no
 * account, so the account's own list never showed them and they looked lost. Opening one
 * adds it to the account (it moves into the list above, with its group or sub-agent chats);
 * the rest stay here until they are added or deleted. Nothing to show (signed out, or none
 * left): the section isn't there at all.
 */
export function LocalChats({ onOpenChat }: { onOpenChat: (chat: { id: string; name: string }) => void }) {
  const [rows, setRows] = useState<LocalChatRow[]>([]);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [busy, setBusy] = useState<string | null>(null);
  const busyRef = useRef(false);
  const { confirm, alert } = useConfirmModal();
  const { menu, open: openMenu, close: closeMenu } = useContextMenuState<LocalChatRow>();

  const load = useCallback(async () => {
    try {
      const res = await getApi()?.list_local_chats?.();
      if (res) setRows(res.chats ?? []);
    } catch {
      /* the list above is unaffected: keep what's shown */
    }
  }, []);

  // Signing in or out, and any change to the chats, changes what is left here.
  useChatsChanged(load);
  useEffect(() => {
    void load();
    return subscribePanelPush((event) => {
      if (event.type === "duckyos_account_changed") void load();
    });
  }, [load]);

  if (!rows.length) return null;

  const toggleCollapsed = () => {
    setCollapsed((c) => {
      writeCollapsed(!c);
      return !c;
    });
  };

  /** One move or delete at a time: a second press while one runs does nothing. */
  const run = async (what: string, work: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(what);
    try {
      await work();
    } catch (err) {
      await alert({ title: "That didn't work", message: err instanceof Error ? err.message : "Try again in a moment." });
    } finally {
      busyRef.current = false;
      setBusy(null);
      void load();
    }
  };

  const add = (row: LocalChatRow) =>
    run(row.id, async () => {
      const res = await getApi()?.adopt_local_chats?.([row.id]);
      if (!res?.moved?.includes(row.id)) {
        await alert({
          title: "Couldn't add it to your account",
          message: res?.error || "It is still here, unchanged. Try again in a moment.",
        });
        return;
      }
      setRows((list) => list.filter((r) => r.id !== row.id));
      onOpenChat({ id: row.id, name: row.title || "Untitled chat" });
    });

  const addAll = async () => {
    const ok = await confirm({
      title: "Add to your account",
      message:
        rows.length === 1
          ? "Add this chat to your account? It moves into your list above."
          : `Add all ${rows.length} to your account? They move into your list above, with any archived ones.`,
      confirmLabel: rows.length === 1 ? "Add" : "Add all",
    });
    if (ok !== true) return;
    await run(ALL, async () => {
      const res = await getApi()?.adopt_local_chats?.(null);
      if (res?.ok === false || res?.failed?.length) {
        await alert({
          title: "Some couldn't be added",
          message: res?.error || "The ones still listed here are unchanged. Try again in a moment.",
        });
      }
    });
  };

  const remove = async (row: LocalChatRow) => {
    const name = row.title || "Untitled chat";
    const ok = await confirm({
      title: "Delete permanently",
      message:
        row.chats > 1
          ? `Permanently delete "${name}" and the ${row.chats - 1} with it? This cannot be undone.`
          : `Permanently delete "${name}"? This cannot be undone.`,
      confirmLabel: "Delete permanently",
      danger: true,
    });
    if (ok !== true) return;
    await run(row.id, async () => {
      const res = await getApi()?.delete_local_chats?.([row.id]);
      if (res?.ok === false) {
        await alert({ title: "Couldn't delete it", message: res.error || "Try again in a moment." });
        return;
      }
      setRows((list) => list.filter((r) => r.id !== row.id));
    });
  };

  return (
    <div className={`other-duckies local-chats${collapsed ? " is-collapsed" : ""}`} aria-label="Chats made on this PC before you signed in">
      <div
        className="other-duckies__head"
        role="button"
        tabIndex={0}
        aria-expanded={!collapsed}
        title={collapsed ? "Show the chats made before you signed in" : "Hide them"}
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
        <span className="other-duckies__title">Not in your account</span>
        <span className="other-duckies__count">{rows.length}</span>
        <button
          type="button"
          className="other-duckies__sync"
          title="Add all to your account"
          aria-label="Add all to your account"
          disabled={busy !== null}
          onClick={(e) => {
            e.stopPropagation();
            void addAll();
          }}
        >
          <Icons.Plus />
        </button>
      </div>
      <div className={`other-duckies__clip${collapsed ? "" : " is-open"}`} aria-hidden={collapsed || undefined}>
        <div className="other-duckies__clip-inner">
          <div className="other-duckies__body local-chats__body">
            <p className="local-chats__hint">Made on this PC while signed out. Open one to add it to your account.</p>
            {rows.map((row) => {
              const working = busy === row.id || busy === ALL;
              return (
                <button
                  key={row.id}
                  type="button"
                  className={`other-duckies__chat local-chats__chat${working ? " is-busy" : ""}`}
                  title="Open and add to your account. Right-click to delete."
                  disabled={busy !== null}
                  onClick={() => void add(row)}
                  onContextMenu={(e) => openMenu(e, row)}
                >
                  {row.group ? <Icons.Users /> : <Icons.Chat />}
                  <span className="other-duckies__chat-title">{row.title || "Untitled chat"}</span>
                  <span className="other-duckies__meta">{working ? "Adding…" : localChatMeta(row)}</span>
                </button>
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
            { id: "add", label: "Open and add to your account", onClick: () => void add(menu.data) },
            { id: "delete", label: "Delete permanently", danger: true, onClick: () => void remove(menu.data) },
          ]}
        />
      ) : null}
    </div>
  );
}
