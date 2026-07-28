import { useCallback, useEffect, useState } from "react";
import "./groupchat.css";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { installPanelPushBus, subscribePanelPush } from "../../hooks/usePanelPushBus";
import { requestOpenDiscordTab } from "../../navigation/openDiscordTab";
import { requestOpenSettings } from "../../navigation/openSettingsTab";
import type { DiscordBotDto, DiscordStatusDto } from "../../types/panel";
import { commandHelp } from "./discordCommands";

// Left-rail control center for everything Discord: connection state, the command
// reference, and a launcher for the chat. The live chat lives in the "Discord
// Ducky" panel/tab; this is the home + cheatsheet.

function botStateLabel(b: DiscordBotDto): string {
  if (!b.has_token) return "no token";
  if (!b.enabled) return "disabled";
  return "ready";
}

export function DiscordHubPanel() {
  const [status, setStatus] = useState<DiscordStatusDto | null>(null);
  const [bots, setBots] = useState<DiscordBotDto[]>([]);
  const [activeBotId, setActiveBotId] = useState("default");

  const refresh = useCallback(() => {
    const api = getApi();
    if (!api) return;
    void api.discord_list_bots?.().then((res) => {
      const list = res.bots || [];
      setBots(list);
      setActiveBotId((prev) => {
        if (list.some((b) => b.id === prev)) return prev;
        const firstReady = list.find((b) => b.enabled && b.has_token);
        return firstReady?.id || list[0]?.id || "default";
      });
    });
  }, []);

  useEffect(() => onApiReady(() => refresh()), [refresh]);
  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type === "discord_changed") refresh();
    });
  }, [refresh]);

  useEffect(() => {
    const api = getApi();
    if (!api) return;
    void api.discord_status(activeBotId).then(setStatus);
  }, [activeBotId]);

  const connected = !!status?.ok;
  const locked = connected && !status?.allowed_ids?.trim();
  const prefix = status?.prefix || "!ducky";
  const commands = commandHelp(prefix);
  const active = bots.find((b) => b.id === activeBotId);

  return (
    <div className="discordhub">
      <div className={`discordhub-status ${connected ? "is-ok" : "is-off"}`}>
        <span className="discordhub-dot" aria-hidden />
        {connected
          ? `Connected as ${status?.bot_name ?? "bot"}`
          : active?.has_token
            ? "Not connected"
            : "No bot token set"}
      </div>

      {bots.length > 0 ? (
        <div className="discordhub-bots">
          {bots.map((b) => {
            const state = botStateLabel(b);
            const selected = b.id === activeBotId;
            return (
              <button
                key={b.id}
                type="button"
                className={`discordhub-bot-row${selected ? " is-selected" : ""}`}
                onClick={() => {
                  setActiveBotId(b.id);
                  requestOpenDiscordTab(b.id, b.label);
                }}
              >
                <span className="discordhub-bot-name">{b.label || b.id}</span>
                <span className="discordhub-bot-meta">
                  {b.prefix ? `${b.prefix} · ` : ""}
                  {state}
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        <p className="groupchat-hint">Add a bot in Settings → Discord (~3 min).</p>
      )}

      <button
        type="button"
        className="settings-btn discordhub-open"
        onClick={() => {
          const target = bots.find((b) => b.id === activeBotId) || bots.find((b) => b.has_token);
          if (target) requestOpenDiscordTab(target.id, target.label);
          else requestOpenSettings("Discord");
        }}
      >
        {active?.has_token ? "Open chat" : "Open Settings → Discord"}
      </button>

      <div className="discordhub-section-title">Commands</div>
      <p className="groupchat-hint">
        Type these in Discord — in the channel you last opened for that bot, while the app runs:
      </p>
      <div className="discordhub-cmds">
        {commands.map((c) => (
          <div key={c.cmd} className="discordhub-cmd">
            <code className="discordhub-code">{c.cmd}</code>
            <span className="discordhub-cmd-desc">{c.desc}</span>
          </div>
        ))}
      </div>

      {locked ? (
        <p className="groupchat-hint discordhub-lock">
          ⚠ Commands are locked — add your Discord user ID under Command access in Settings →
          Discord to enable them.
        </p>
      ) : null}
    </div>
  );
}
