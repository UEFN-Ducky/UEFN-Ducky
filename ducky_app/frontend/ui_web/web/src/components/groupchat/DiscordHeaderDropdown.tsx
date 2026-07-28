import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { installAgentEventBus, subscribeAgentEvents } from "../../hooks/useAgentEventBus";
import { installPanelPushBus, subscribePanelPush } from "../../hooks/usePanelPushBus";
import { requestOpenDiscordTab } from "../../navigation/openDiscordTab";
import { requestOpenSettings } from "../../navigation/openSettingsTab";
import { applySettingsHistory } from "../../navigation/settingsHistory";
import { Icons } from "../../icons/Icons";
import type { DiscordBotDto } from "../../types/panel";
import {
  formatDiscordBadge,
  bumpDiscordActivity,
  clearDiscordActivity,
  useDiscordActivityMap,
  useDiscordActivityTotal,
} from "./discordActivity";

/** Must match DiscordBotsCatalog — in-memory draft, not persisted until Save. */
const DISCORD_NEW_BOT_KEY = "__new__";

const MENU_WIDTH = 260;
const MENU_GAP = 6;

function computeMenuPosition(trigger: HTMLElement): { top: number; left: number } {
  const rect = trigger.getBoundingClientRect();
  let left = rect.right - MENU_WIDTH;
  if (left < 8) left = 8;
  if (left + MENU_WIDTH > window.innerWidth - 8) {
    left = Math.max(8, window.innerWidth - MENU_WIDTH - 8);
  }
  return { top: rect.bottom + MENU_GAP, left };
}

type DiscordHeaderDropdownProps = {
  icon: ReactNode;
  title: string;
  active?: boolean;
};

export function DiscordHeaderDropdown({ icon, title, active }: DiscordHeaderDropdownProps) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null);
  const [bots, setBots] = useState<DiscordBotDto[]>([]);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const activityMap = useDiscordActivityMap();
  const total = useDiscordActivityTotal();
  const badge = formatDiscordBadge(total);

  const refreshBots = () => {
    const api = getApi();
    if (!api?.discord_list_bots) return;
    void api.discord_list_bots().then((list) => {
      const seen = new Set<string>();
      const next: DiscordBotDto[] = [];
      for (const b of list.bots || []) {
        const id = (b.id || "").trim();
        if (!id || seen.has(id)) continue;
        seen.add(id);
        next.push(b);
      }
      setBots(next);
    });
  };

  const addBotFromHeader = () => {
    requestOpenSettings("Discord");
    window.dispatchEvent(
      new CustomEvent("ducky:settings-section", { detail: { tab: "Discord", section: "bots" } }),
    );
    queueMicrotask(() =>
      applySettingsHistory({
        kind: "settings",
        tab: "Discord",
        sectionTab: "bots",
        drill: { type: "discord", botId: DISCORD_NEW_BOT_KEY },
        name: "New bot",
      }),
    );
    setOpen(false);
  };

  useEffect(() => onApiReady(refreshBots), []);
  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type === "discord_changed") refreshBots();
    });
  }, []);
  useEffect(() => {
    installAgentEventBus();
    return subscribeAgentEvents((event) => {
      if (event.type === "discord_message") {
        bumpDiscordActivity(event.bot_id || "default");
      }
    });
  }, []);

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) {
      setMenuPos((pos) => (pos === null ? pos : null));
      return;
    }
    refreshBots();
    const update = () => {
      const trigger = triggerRef.current;
      if (!trigger) return;
      setMenuPos(computeMenuPosition(trigger));
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const openBot = (bot: DiscordBotDto) => {
    clearDiscordActivity(bot.id);
    requestOpenDiscordTab(bot.id, bot.label);
    setOpen(false);
  };

  const openBotSettings = (bot: DiscordBotDto) => {
    const label = bot.label || bot.id;
    requestOpenSettings("Discord");
    window.dispatchEvent(
      new CustomEvent("ducky:settings-section", { detail: { tab: "Discord", section: "bots" } }),
    );
    queueMicrotask(() =>
      applySettingsHistory({
        kind: "settings",
        tab: "Discord",
        sectionTab: "bots",
        drill: { type: "discord", botId: bot.id },
        name: label,
      }),
    );
    setOpen(false);
  };

  const menu =
    open && menuPos ? (
      <div
        ref={menuRef}
        className="terminal-header-menu terminal-header-menu--portaled no-drag"
        style={{ top: menuPos.top, left: menuPos.left }}
      >
        {bots.length === 0 ? (
          <button
            type="button"
            className="terminal-header-new-btn"
            onClick={addBotFromHeader}
          >
            <span>Create first bot</span>
          </button>
        ) : (
          <>
            <div className="terminal-header-list">
              {bots.map((bot) => {
                const count = activityMap.get(bot.id) || 0;
                const rowBadge = formatDiscordBadge(count);
                const label = bot.label || bot.id;
                return (
                  <div key={bot.id} className="terminal-header-item">
                    <button
                      type="button"
                      className="terminal-header-item-main"
                      onClick={() => openBot(bot)}
                    >
                      <span className="terminal-header-item-name">{label}</span>
                      <span className="terminal-header-item-meta">
                        {bot.prefix ? (
                          <span className="terminal-header-item-shell">{bot.prefix}</span>
                        ) : null}
                        <span className="terminal-header-item-status">
                          {!bot.has_token ? "no token" : !bot.enabled ? "disabled" : "ready"}
                        </span>
                      </span>
                    </button>
                    {rowBadge ? <span className="discord-header-row-badge">{rowBadge}</span> : null}
                    <button
                      type="button"
                      className="terminal-header-item-close icon-btn no-drag"
                      title={`Settings · ${label}`}
                      aria-label={`Settings for ${label}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        openBotSettings(bot);
                      }}
                    >
                      <Icons.Settings />
                    </button>
                  </div>
                );
              })}
            </div>
            <button type="button" className="terminal-header-new-btn" onClick={addBotFromHeader}>
              <span>Add bot</span>
            </button>
          </>
        )}
      </div>
    ) : null;

  return (
    <div className="terminal-header-root">
      <button
        ref={triggerRef}
        type="button"
        className={`icon-btn no-drag plugin-header-btn terminal-header-trigger${open || active ? " is-active" : ""}`}
        title={title}
        aria-label={title}
        aria-pressed={active || undefined}
        onClick={() => setOpen(!open)}
      >
        {icon}
        {badge ? <span className="terminal-header-badge">{badge}</span> : null}
      </button>
      {menu ? createPortal(menu, document.body) : null}
    </div>
  );
}
