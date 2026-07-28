import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
} from "react";

import {
  CatalogDetailHead,
  CatalogSlideShell,
  useCatalogSlideNav,
} from "../../components/catalog-slide";
import { TruncatedText } from "../../components/TruncatedText";
import { UnsavedChangesModal } from "../../components/UnsavedChangesModal";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { Icons } from "../../icons/Icons";
import type { SettingsNavLocation } from "../../navigation/settingsHistory";
import {
  useApplySettingsDrill,
  useRecordSettingsLocation,
  useSettingsHistoryBack,
} from "../../navigation/useSettingsHistory";
import type { DiscordBotDto, DiscordStatusDto } from "../../types/panel";
import { DiscordSetupGuide } from "./AgentTab";
import { PluginSettingsSections } from "./PluginSettingsSections";

/** In-memory draft — never persisted until Save. */
export const DISCORD_NEW_BOT_KEY = "__new__";

function dedupeBots(list: DiscordBotDto[]): DiscordBotDto[] {
  const seen = new Set<string>();
  const out: DiscordBotDto[] = [];
  for (const b of list) {
    const id = (b.id || "").trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push(b);
  }
  return out;
}

function blankBotDraft(existingCount: number): DiscordBotDto {
  const n = existingCount + 1;
  return {
    id: DISCORD_NEW_BOT_KEY,
    label: n === 1 ? "Discord Bot" : `Discord Bot ${n}`,
    guild_id: "",
    post_as: "",
    allowed_ids: "",
    prefix: n === 1 ? "!ducky" : `!bot${n}`,
    enabled: true,
    show_offline: false,
    has_token: false,
    configured: false,
  };
}

/** Full-tab list → slide-in detail (same CatalogSlideShell pattern as Skills / MCP / Plans). */
export function DiscordBotsCatalog() {
  const { confirm } = useConfirmModal();
  const [bots, setBots] = useState<DiscordBotDto[]>([]);
  const [loadError, setLoadError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [dirty, setDirty] = useState(false);
  const [leavePrompt, setLeavePrompt] = useState(false);
  const [leaveSaving, setLeaveSaving] = useState(false);
  const saveRef = useRef<(() => Promise<boolean>) | null>(null);
  const pendingOpenRef = useRef<string | null>(null);

  const {
    selectedKey,
    setSelectedKey,
    detailOpen,
    detailRendered,
    openDetail,
    closeDetail,
  } = useCatalogSlideNav();

  const isCreate = selectedKey === DISCORD_NEW_BOT_KEY;

  const discordNavLoc = useMemo<SettingsNavLocation>(() => {
    if (!selectedKey) {
      return { kind: "settings", tab: "Discord", sectionTab: "bots", name: "Discord · Bots" };
    }
    if (selectedKey === DISCORD_NEW_BOT_KEY) {
      return {
        kind: "settings",
        tab: "Discord",
        sectionTab: "bots",
        drill: { type: "discord", botId: DISCORD_NEW_BOT_KEY },
        name: "New bot",
      };
    }
    const label = bots.find((b) => b.id === selectedKey)?.label || selectedKey;
    return {
      kind: "settings",
      tab: "Discord",
      sectionTab: "bots",
      drill: { type: "discord", botId: selectedKey },
      name: label,
    };
  }, [selectedKey, bots]);
  useRecordSettingsLocation(discordNavLoc);

  const applyDiscordDrill = useCallback(
    (loc: SettingsNavLocation) => {
      if (loc.tab !== "Discord") return;
      const botId = loc.drill?.type === "discord" ? loc.drill.botId : null;
      if (!botId) {
        closeDetail();
        return;
      }
      if (botId === DISCORD_NEW_BOT_KEY) {
        setSelectedKey(DISCORD_NEW_BOT_KEY);
        return;
      }
      if (bots.length > 0 && !bots.some((b) => b.id === botId)) {
        closeDetail();
        return;
      }
      setSelectedKey(botId);
    },
    [bots, closeDetail, setSelectedKey],
  );
  useApplySettingsDrill("Discord", applyDiscordDrill);

  const historyCloseDetail = useSettingsHistoryBack(closeDetail);

  const refresh = useCallback(() => {
    const api = getApi();
    if (!api?.discord_list_bots) return;
    void api.discord_list_bots().then((res) => {
      if (!res.ok) {
        setLoadError(res.error || "Could not load bots");
        setBots([]);
        return;
      }
      setLoadError("");
      setBots(dedupeBots(res.bots || []));
    });
  }, []);

  useEffect(() => onApiReady(() => refresh()), [refresh]);

  const selected = useMemo(() => {
    if (!selectedKey) return null;
    if (selectedKey === DISCORD_NEW_BOT_KEY) return blankBotDraft(bots.length);
    return bots.find((b) => b.id === selectedKey) || null;
  }, [bots, selectedKey]);

  const finishLeave = useCallback(
    (nextKey: string | null) => {
      setLeavePrompt(false);
      setDirty(false);
      pendingOpenRef.current = null;
      if (nextKey) openDetail(nextKey);
      else historyCloseDetail();
    },
    [historyCloseDetail, openDetail],
  );

  const requestCloseDetail = useCallback(() => {
    if (!dirty) {
      finishLeave(null);
      return;
    }
    pendingOpenRef.current = null;
    setLeavePrompt(true);
  }, [dirty, finishLeave]);

  const openBotDetail = useCallback(
    (id: string) => {
      if (dirty && selectedKey && selectedKey !== id) {
        pendingOpenRef.current = id;
        setLeavePrompt(true);
        return;
      }
      openDetail(id);
    },
    [dirty, openDetail, selectedKey],
  );

  const startNewBot = () => {
    openBotDetail(DISCORD_NEW_BOT_KEY);
  };

  const toggleEnabled = async (bot: DiscordBotDto) => {
    const api = getApi();
    if (!api?.discord_save_bot) return;
    setBusyId(bot.id);
    try {
      await api.discord_save_bot({ id: bot.id, enabled: !bot.enabled });
      refresh();
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId("");
    }
  };

  const deleteBot = async (bot: DiscordBotDto) => {
    const api = getApi();
    if (!api?.discord_delete_bot) return;
    const ok = await confirm({
      title: "Delete Discord bot?",
      message: `Remove “${bot.label}” from this PC? The Discord application itself is unchanged — only the local token/profile is deleted.`,
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    setBusyId(bot.id);
    try {
      await api.discord_delete_bot(bot.id);
      if (selectedKey === bot.id) closeDetail();
      refresh();
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId("");
    }
  };

  const handleLeaveSave = async () => {
    setLeaveSaving(true);
    try {
      const ok = (await saveRef.current?.()) ?? false;
      if (ok) finishLeave(pendingOpenRef.current);
    } finally {
      setLeaveSaving(false);
    }
  };

  const handleLeaveDiscard = () => {
    finishLeave(pendingOpenRef.current);
  };

  const listHeader = (
    <div className="catalog-slide-header">
      <div className="catalog-slide-header-titles">
        <h2 className="catalog-slide-title">Discord</h2>
        <p className="general-tab-section-desc">
          Run one or more Discord bots from this PC — each with its own token, server, and command
          prefix. Open a bot to fill in its details.
        </p>
      </div>
      <div className="catalog-slide-header-actions">
        <button type="button" className="settings-btn" onClick={startNewBot}>
          {bots.length === 0 ? "Create first bot" : "Add bot"}
        </button>
      </div>
    </div>
  );

  const listBody = (
    <>
      <PluginSettingsSections pluginId="discord" accordion />
      {loadError ? <p className="settings-inline-error">{loadError}</p> : null}
      <section className="discord-bots-section" aria-label="Discord bots">
        {bots.length === 0 ? (
          <p className="catalog-slide-empty">
            No bots yet —{" "}
            <button type="button" className="discord-bots-empty-link" onClick={startNewBot}>
              Create first bot
            </button>
            , then paste a token and server ID and Save.
          </p>
        ) : (
          <table className="discord-bots-table">
            <thead>
              <tr>
                <th scope="col">Bot</th>
                <th scope="col">Prefix</th>
                <th scope="col">Status</th>
                <th scope="col">Enabled</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {bots.map((bot) => (
                <tr
                  key={bot.id}
                  className={selectedKey === bot.id ? "is-selected" : undefined}
                  onClick={() => openBotDetail(bot.id)}
                >
                  <td className="discord-bots-table-name">{bot.label}</td>
                  <td className="discord-bots-table-prefix">{bot.prefix || "!ducky"}</td>
                  <td className="discord-bots-table-status">
                    {bot.has_token
                      ? bot.guild_id
                        ? `server ${bot.guild_id.slice(0, 8)}…`
                        : "Token saved"
                      : "No token yet"}
                  </td>
                  <td className="discord-bots-table-enabled" onClick={(e) => e.stopPropagation()}>
                    <label
                      className="mcp-plugin-enable"
                      htmlFor={`discord-bot-enable-${bot.id}`}
                    >
                      <span className="general-tab-switch general-tab-switch--compact">
                        <input
                          id={`discord-bot-enable-${bot.id}`}
                          type="checkbox"
                          className="general-tab-switch-input"
                          checked={bot.enabled}
                          disabled={busyId === bot.id}
                          onChange={() => void toggleEnabled(bot)}
                          aria-label={`${bot.label} enable`}
                        />
                        <span className="general-tab-switch-track" aria-hidden />
                      </span>
                    </label>
                  </td>
                  <td className="discord-bots-table-actions" onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      className="icon-btn no-drag"
                      title={`Settings · ${bot.label}`}
                      aria-label={`Settings for ${bot.label}`}
                      onClick={() => openBotDetail(bot.id)}
                    >
                      <Icons.Settings />
                    </button>
                    <button
                      type="button"
                      className="icon-btn no-drag"
                      title={`Delete · ${bot.label}`}
                      aria-label={`Delete ${bot.label}`}
                      disabled={busyId === bot.id}
                      onClick={() => void deleteBot(bot)}
                    >
                      <Icons.Trash />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );

  return (
    <>
      <CatalogSlideShell
        className="discord-tab"
        detailOpen={detailOpen}
        detailRendered={detailRendered}
        listAriaLabel="Discord bots"
        listHeader={listHeader}
        listBody={listBody}
        detailHead={
          selected ? (
            <CatalogDetailHead
              onBack={requestCloseDetail}
              backAriaLabel="Back to bots list"
              breadcrumbs={[
                { id: "bots", label: "Bots", onClick: requestCloseDetail },
                {
                  id: selected.id,
                  label: isCreate ? "New bot" : selected.label,
                  current: true,
                },
              ]}
              actions={
                !isCreate ? (
                  <button
                    type="button"
                    className="settings-btn"
                    disabled={busyId === selected.id}
                    onClick={() => void deleteBot(selected)}
                  >
                    Delete
                  </button>
                ) : null
              }
            />
          ) : null
        }
        detailBody={
          selected ? (
            <BotDetailForm
              key={selected.id}
              bot={selected}
              isCreate={isCreate}
              onSaved={(saved) => {
                setDirty(false);
                refresh();
                if (saved?.id) openDetail(saved.id);
              }}
              onDirtyChange={setDirty}
              saveRef={saveRef}
            />
          ) : null
        }
        detailPlaceholder={
          <p className="general-tab-section-desc">
            Select a bot to edit its token, server, and command prefix.
          </p>
        }
      />
      {leavePrompt ? (
        <UnsavedChangesModal
          message={
            <>
              This bot has <strong>unsaved changes</strong>. Save before leaving?
            </>
          }
          saving={leaveSaving}
          onSave={() => void handleLeaveSave()}
          onDiscard={handleLeaveDiscard}
          onCancel={() => setLeavePrompt(false)}
        />
      ) : null}
    </>
  );
}

const BotDetailForm = memo(function BotDetailForm({
  bot,
  isCreate,
  onSaved,
  onDirtyChange,
  saveRef,
}: {
  bot: DiscordBotDto;
  isCreate: boolean;
  onSaved: (bot?: DiscordBotDto) => void;
  onDirtyChange: (dirty: boolean) => void;
  saveRef: MutableRefObject<(() => Promise<boolean>) | null>;
}) {
  const [token, setToken] = useState("");
  const [label, setLabel] = useState(bot.label);
  const [guild, setGuild] = useState(bot.guild_id || "");
  const [postName, setPostName] = useState(bot.post_as || "");
  const [allowedIds, setAllowedIds] = useState(bot.allowed_ids || "");
  const [prefix, setPrefix] = useState(bot.prefix || "!ducky");
  const [showOffline, setShowOffline] = useState(Boolean(bot.show_offline));
  const [status, setStatus] = useState<DiscordStatusDto | null>(null);
  const [busy, setBusy] = useState(false);
  const [diag, setDiag] = useState<Awaited<
    ReturnType<NonNullable<ReturnType<typeof getApi>>["discord_debug"]>
  > | null>(null);

  useEffect(() => {
    setLabel(bot.label);
    setGuild(bot.guild_id || "");
    setPostName(bot.post_as || "");
    setAllowedIds(bot.allowed_ids || "");
    setPrefix(bot.prefix || "!ducky");
    setShowOffline(Boolean(bot.show_offline));
    setToken("");
    setDiag(null);
    onDirtyChange(false);
    if (isCreate) {
      setStatus(null);
      return;
    }
    const api = getApi();
    if (api) void api.discord_status(bot.id).then(setStatus);
  }, [
    bot.id,
    bot.label,
    bot.guild_id,
    bot.post_as,
    bot.allowed_ids,
    bot.prefix,
    bot.show_offline,
    isCreate,
    onDirtyChange,
  ]);

  useEffect(() => {
    const dirty =
      token.trim() !== "" ||
      label !== bot.label ||
      guild !== (bot.guild_id || "") ||
      postName !== (bot.post_as || "") ||
      allowedIds !== (bot.allowed_ids || "") ||
      prefix !== (bot.prefix || "!ducky") ||
      showOffline !== Boolean(bot.show_offline);
    onDirtyChange(dirty);
  }, [
    token,
    label,
    guild,
    postName,
    allowedIds,
    prefix,
    showOffline,
    bot.label,
    bot.guild_id,
    bot.post_as,
    bot.allowed_ids,
    bot.prefix,
    bot.show_offline,
    onDirtyChange,
  ]);

  const saveAndTest = useCallback(async (): Promise<boolean> => {
    const api = getApi();
    if (!api?.discord_save_bot) return false;
    setBusy(true);
    try {
      const res = await api.discord_save_bot({
        ...(isCreate ? { create: true } : { id: bot.id }),
        label: label.trim() || bot.label,
        guild_id: guild.trim(),
        post_as: postName.trim(),
        allowed_ids: allowedIds.trim(),
        prefix: prefix.trim() || "!ducky",
        show_offline: showOffline,
        enabled: bot.enabled,
        token: token.trim() || undefined,
      });
      if (!res.ok) return false;
      setToken("");
      if (res.status) setStatus(res.status);
      else if (res.bot?.id) setStatus(await api.discord_status(res.bot.id));
      onSaved(res.bot);
      return true;
    } finally {
      setBusy(false);
    }
  }, [
    allowedIds,
    bot.enabled,
    bot.id,
    bot.label,
    guild,
    isCreate,
    label,
    onSaved,
    postName,
    prefix,
    showOffline,
    token,
  ]);

  useEffect(() => {
    saveRef.current = saveAndTest;
    return () => {
      saveRef.current = null;
    };
  }, [saveAndTest, saveRef]);

  const runDiag = async () => {
    if (isCreate) return;
    const api = getApi();
    if (api) setDiag(await api.discord_debug(bot.id));
  };

  const openPortal = useCallback(() => {
    const api = getApi();
    if (api) void api.open_external_url("https://discord.com/developers/applications");
  }, []);

  const commandsLocked = status?.ok ? !allowedIds.trim() : false;
  const statusLine = status
    ? status.ok
      ? { text: `Connected as ${status.bot_name ?? "bot"}`, ok: true }
      : status.configured
        ? { text: status.error ?? "Not reachable", ok: false }
        : { text: "Not connected", ok: false }
    : null;

  const connected = Boolean(status?.ok);

  return (
    <div className="discord-bot-detail">
      <details
        key={connected ? "connected" : "setup"}
        className="discord-setup-guide"
        {...(!connected ? ({ open: true } as Record<string, unknown>) : {})}
      >
        <summary>How to set up your bot (one time, ~3 minutes)</summary>
        <DiscordSetupGuide openPortal={openPortal} />
      </details>

      <div className="discord-bot-fields">
        <label className="discord-bot-field">
          <span className="discord-bot-field-label">Label</span>
          <input
            className="settings-input"
            type="text"
            value={label}
            placeholder="Name in this list"
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
        <label className="discord-bot-field">
          <span className={`discord-bot-field-label${status?.ok ? " is-ok" : ""}`}>
            {status?.ok ? <Icons.Check /> : null}
            Bot token
          </span>
          <input
            className="settings-input"
            type="password"
            value={token}
            placeholder={
              !isCreate && (bot.has_token || status?.configured)
                ? "••••••••••••••••"
                : "Paste bot token"
            }
            onChange={(e) => setToken(e.target.value)}
          />
        </label>
        <label className="discord-bot-field">
          <span className="discord-bot-field-label">Server ID</span>
          <input
            className="settings-input"
            type="text"
            value={guild}
            placeholder="Right-click server → Copy Server ID"
            onChange={(e) => setGuild(e.target.value)}
          />
        </label>
        <label className="discord-bot-field">
          <span className="discord-bot-field-label">Command prefix</span>
          <input
            className="settings-input"
            type="text"
            value={prefix}
            placeholder="!ducky"
            onChange={(e) => setPrefix(e.target.value)}
          />
        </label>
        <label className="discord-bot-field">
          <span className="discord-bot-field-label">Post as</span>
          <input
            className="settings-input"
            type="text"
            value={postName}
            placeholder="Optional display name on messages"
            onChange={(e) => setPostName(e.target.value)}
          />
        </label>
        <label className="discord-bot-field">
          <span className="discord-bot-field-label">Command access</span>
          <input
            className="settings-input"
            type="text"
            value={allowedIds}
            placeholder="Your Discord user ID (* = anyone)"
            onChange={(e) => setAllowedIds(e.target.value)}
          />
        </label>
        <label
          className="discord-bot-field discord-bot-field--switch"
          htmlFor={`discord-show-offline-${bot.id}`}
        >
          <span className="discord-bot-field-label">Show offline</span>
          <span className="general-tab-switch general-tab-switch--compact">
            <input
              id={`discord-show-offline-${bot.id}`}
              type="checkbox"
              className="general-tab-switch-input"
              checked={showOffline}
              onChange={(e) => setShowOffline(e.target.checked)}
              aria-label="Show this bot as offline on Discord"
            />
            <span className="general-tab-switch-track" aria-hidden />
          </span>
          <span className="discord-bot-field-hint">
            Off = stay Online in Discord whenever this bot can respond. On = appear Offline.
          </span>
        </label>
      </div>

      {commandsLocked ? (
        <p className="discord-bot-note discord-bot-note--warn">
          Add your Discord user ID above (Developer Mode → right-click yourself → Copy User ID),
          then Save & Test — or type <code>{prefix || "!ducky"} whoami</code> in chat.
        </p>
      ) : null}

      <div className="discord-bot-actions">
        <button
          type="button"
          className="settings-btn"
          disabled={busy}
          onClick={() => void saveAndTest()}
        >
          {busy ? "Saving…" : "Save & Test"}
        </button>
        {!isCreate && status?.ok ? (
          <>
            <button type="button" className="settings-btn" onClick={() => void runDiag()}>
              Diagnose {prefix || "!ducky"}
            </button>
            <button
              type="button"
              className="discord-setup-link"
              onClick={() => {
                const api = getApi();
                if (api) void api.discord_open_portal(bot.id);
              }}
            >
              Edit bot name & avatar ↗
            </button>
          </>
        ) : null}
      </div>

      {statusLine ? (
        <div className={`llms-provider-status ${statusLine.ok ? "is-ok" : "is-fail"}`}>
          {statusLine.ok ? <Icons.Check /> : null}
          <TruncatedText title={statusLine.text} className="llms-provider-status-text">
            {statusLine.text}
          </TruncatedText>
        </div>
      ) : null}

      {diag ? (
        <div className={`llms-provider-status ${diag.poller_alive ? "is-ok" : "is-fail"}`}>
          <TruncatedText title={JSON.stringify(diag)} className="llms-provider-status-text">
            {diag.poller_alive
              ? `Watching #${diag.watching_channel_name || diag.watching_channel_id || "(none)"} · ${
                  diag.last_seen
                    ? `last saw “${diag.last_seen.preview ?? ""}” from ${diag.last_seen.author}`
                    : "no messages seen yet"
                }`
              : "Poller not running — hit Save & Test, or restart the app."}
          </TruncatedText>
        </div>
      ) : null}
    </div>
  );
});
