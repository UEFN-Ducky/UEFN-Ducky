import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import "./groupchat.css";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { installAgentEventBus, subscribeAgentEvents } from "../../hooks/useAgentEventBus";
import { installPanelPushBus, subscribePanelPush } from "../../hooks/usePanelPushBus";
import { ChoiceDropdown } from "../ChoiceDropdown";
import { Icons } from "../../icons/Icons";
import { requestOpenDiscordTab } from "../../navigation/openDiscordTab";
import { setDiscordViewingBot } from "./discordActivity";
import type {
  DiscordBotDto,
  DiscordChannelDto,
  DiscordMemberGroupDto,
  DiscordMemberStatus,
  DiscordMessageDto,
  DiscordStatusDto,
} from "../../types/panel";

import { commandHelp } from "./discordCommands";

// The whole Discord Ducky panel: channel picker + live message list + composer.
// One shared backend client (backend/discord) polls the open channel and pushes
// `discord_message` events; we hydrate history via discord_open_channel.
//
// variant="panel" — compact dock rail (channel dropdown, single column).
// variant="full"  — roomy editor tab / focus window (channels + members columns).

function appendUnique(list: DiscordMessageDto[], msg: DiscordMessageDto): DiscordMessageDto[] {
  if (list.some((m) => m.id === msg.id)) return list;
  return [...list, msg];
}

function formatTime(ts: number): string {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

/** IRC-style deterministic nick hue: same person, same color, every session. */
function hashHue(key: string): number {
  let h = 5381;
  for (let i = 0; i < key.length; i++) h = ((h << 5) + h + key.charCodeAt(i)) >>> 0;
  return h % 360;
}

/** Discord role color int → CSS #rrggbb, or null when unset (0). */
function roleColorCss(color: number | null | undefined): string | null {
  if (!color) return null;
  return `#${(color & 0xffffff).toString(16).padStart(6, "0")}`;
}

function memberStyle(id: string, color: number | null | undefined): CSSProperties {
  const css = roleColorCss(color);
  if (css) return { "--member-color": css } as CSSProperties;
  return { "--nick-h": hashHue(id) } as CSSProperties;
}

// The bot relays app-side sends as "**name:** text" (see DiscordStatusDto.post_name).
// Unwrap those so they read as the actual person: right name, own nick color,
// correct message grouping — instead of one endless bot monologue.
const RELAY_RE = /^\*\*([^*\n]{1,64}?):\*\*[ \t]?([\s\S]*)$/;

type ViewMsg = {
  id: string;
  author: string;
  /** Stable key for the nick color hash (author id, or name for relayed users). */
  colorKey: string;
  bot: boolean;
  /** Bot name that relayed this app-user message, if any. */
  relayedBy?: string;
  body: string;
  ts: number;
};

function toViewMsg(m: DiscordMessageDto): ViewMsg {
  const parsed = Date.parse(m.timestamp);
  const ts = Number.isNaN(parsed) ? 0 : parsed;
  if (m.bot) {
    const relay = RELAY_RE.exec(m.content);
    if (relay?.[1]) {
      return {
        id: m.id,
        ts,
        author: relay[1],
        colorKey: relay[1].toLowerCase(),
        bot: false,
        relayedBy: m.author,
        body: relay[2] ?? "",
      };
    }
  }
  return {
    id: m.id,
    ts,
    author: m.author,
    colorKey: (m.author_id || m.author).toLowerCase(),
    bot: m.bot,
    body: m.content,
  };
}

/** Messages from the same person within this window collapse into one group. */
const GROUP_WINDOW_MS = 7 * 60 * 1000;

type Row =
  | { kind: "divider"; id: string; label: string }
  | { kind: "msg"; msg: ViewMsg; first: boolean };

function dayLabel(ts: number): string {
  const d = new Date(ts);
  const now = new Date();
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diffDays = Math.round((startOf(now) - startOf(d)) / 86400000);
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  const opts: Intl.DateTimeFormatOptions = { month: "long", day: "numeric" };
  if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return d.toLocaleDateString([], opts);
}

function buildRows(msgs: ViewMsg[]): Row[] {
  const rows: Row[] = [];
  let prev: ViewMsg | null = null;
  for (const m of msgs) {
    const sameDay =
      !!prev && !!prev.ts && !!m.ts && new Date(prev.ts).toDateString() === new Date(m.ts).toDateString();
    if (m.ts && (!prev || !sameDay)) {
      rows.push({ kind: "divider", id: `day-${m.id}`, label: dayLabel(m.ts) });
    }
    const first =
      !prev ||
      !sameDay ||
      prev.author !== m.author ||
      prev.bot !== m.bot ||
      m.ts - prev.ts > GROUP_WINDOW_MS;
    rows.push({ kind: "msg", msg: m, first });
    prev = m;
  }
  return rows;
}

// ---- Chat-lite inline rendering (no HTML injection — React nodes only) ------
// Supports **bold**, `code`, custom Discord emoji, #channel / @user mentions,
// and plain links. Everything else stays literal text.

const INLINE_TOKEN_RE =
  /(\*\*[^*\n]+\*\*|`[^`\n]+`|<a?:\w{2,}:\d{6,}>|<#\d{6,}>|<@!?\d{6,}>|https?:\/\/[^\s<>]+|@everyone|@here)/g;
const EMOJI_TOKEN_RE = /^<(a?):(\w{2,}):(\d{6,})>$/;

/** True when the message is only custom/unicode emoji — rendered jumbo-sized. */
function isEmojiOnly(body: string): boolean {
  if (!body.trim()) return false;
  const withoutCustom = body.replace(/<a?:\w{2,}:\d{6,}>/g, "");
  if (withoutCustom === body && !/\p{Extended_Pictographic}/u.test(body)) return false;
  // Pictographs plus the glue of emoji sequences: ZWJ, VS-16, skin tones, flags.
  return /^[\p{Extended_Pictographic}\u200d\ufe0f\u{1F3FB}-\u{1F3FF}\u{1F1E6}-\u{1F1FF}\s]*$/u.test(
    withoutCustom,
  );
}

function renderInline(body: string, channelById: Map<string, string>): ReactNode[] {
  const out: ReactNode[] = [];
  const parts = body.split(INLINE_TOKEN_RE);
  parts.forEach((part, i) => {
    if (!part) return;
    const key = `t${i}`;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      out.push(<strong key={key}>{part.slice(2, -2)}</strong>);
      return;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      out.push(
        <code key={key} className="groupchat-code">
          {part.slice(1, -1)}
        </code>,
      );
      return;
    }
    const emoji = EMOJI_TOKEN_RE.exec(part);
    if (emoji?.[2] && emoji[3]) {
      const ext = emoji[1] ? "gif" : "webp";
      out.push(
        <img
          key={key}
          className="groupchat-emoji"
          src={`https://cdn.discordapp.com/emojis/${emoji[3]}.${ext}?size=48`}
          alt={`:${emoji[2]}:`}
          title={`:${emoji[2]}:`}
          loading="lazy"
          draggable={false}
        />,
      );
      return;
    }
    const channel = /^<#(\d{6,})>$/.exec(part);
    if (channel?.[1]) {
      const name = channelById.get(channel[1]);
      out.push(
        <span key={key} className="groupchat-mention">
          #{name ?? "channel"}
        </span>,
      );
      return;
    }
    if (/^<@!?\d{6,}>$/.test(part)) {
      out.push(
        <span key={key} className="groupchat-mention">
          @user
        </span>,
      );
      return;
    }
    if (part === "@everyone" || part === "@here") {
      out.push(
        <span key={key} className="groupchat-mention">
          {part}
        </span>,
      );
      return;
    }
    if (/^https?:\/\//.test(part)) {
      out.push(
        <a key={key} className="groupchat-link" href={part} target="_blank" rel="noopener noreferrer">
          {part}
        </a>,
      );
      return;
    }
    out.push(part);
  });
  return out;
}

const MAX_COMPOSER_HEIGHT_PX = 132;

export function GroupChatPanel({
  variant = "panel",
  botId: botIdProp,
}: {
  variant?: "panel" | "full";
  /** Locked bot for an editor tab (`discord:<botId>`). Dock panel omits this. */
  botId?: string;
}) {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [statusError, setStatusError] = useState<string>("");
  const [status, setStatus] = useState<DiscordStatusDto | null>(null);
  const [bots, setBots] = useState<DiscordBotDto[]>([]);
  const [botId, setBotId] = useState<string>(botIdProp || "default");
  const [channels, setChannels] = useState<DiscordChannelDto[]>([]);
  const [activeChannel, setActiveChannel] = useState<string>("");
  const [messages, setMessages] = useState<DiscordMessageDto[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [pendingNew, setPendingNew] = useState(0);
  const [memberGroups, setMemberGroups] = useState<DiscordMemberGroupDto[]>([]);
  const [membersReady, setMembersReady] = useState(false);
  const [membersError, setMembersError] = useState("");

  const activeChannelRef = useRef(activeChannel);
  activeChannelRef.current = activeChannel;
  const botIdRef = useRef(botId);
  botIdRef.current = botId;
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const nearBottomRef = useRef(true);
  const prevCountRef = useRef(0);
  const prevChannelRef = useRef("");

  // Editor tab owns the bot id; dock panel can pick freely.
  useEffect(() => {
    if (botIdProp) setBotId(botIdProp);
  }, [botIdProp]);

  useEffect(() => {
    setDiscordViewingBot(botId);
    return () => {
      if (botIdRef.current === botId) setDiscordViewingBot(null);
    };
  }, [botId]);

  const refresh = useCallback(() => {
    const api = getApi();
    if (!api) return;
    void api.discord_list_bots?.().then((list) => {
      // Always keep every profile so an unset bot can still switch back to a working one.
      setBots(list.bots || []);
      if (botIdProp) return;
      setBotId((prev) => {
        const ids = (list.bots || []).map((b) => b.id);
        if (ids.includes(prev)) return prev;
        const firstReady = (list.bots || []).find((b) => b.enabled && b.has_token);
        return firstReady?.id || ids[0] || "default";
      });
    });
    const bid = botIdRef.current;
    void api.discord_status(bid).then((s) => {
      if (botIdRef.current !== bid) return; // stale — user switched bots
      setStatus(s);
      setConfigured(!!s.configured);
      setStatusError(s.error ?? "");
      if (!s.configured) return;
      void api.discord_list_channels(bid).then((res) => {
        if (botIdRef.current !== bid) return;
        if (res.ok && res.channels) setChannels(res.channels);
        else setError(res.error ?? "Could not list channels");
      });
    });
  }, [botIdProp]);

  useEffect(() => onApiReady(() => refresh()), [refresh]);

  // Re-check when Discord settings save (connecting in Settings lights this up live).
  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type === "discord_changed") refresh();
    });
  }, [refresh]);

  // Switching bots reloads status + channels and clears the open channel.
  useEffect(() => {
    const api = getApi();
    if (!api) return;
    setActiveChannel("");
    setMessages([]);
    setChannels([]);
    setConfigured(null);
    setStatus(null);
    setStatusError("");
    setError("");
    const bid = botId;
    void api.discord_status(bid).then((s) => {
      if (botIdRef.current !== bid) return;
      setStatus(s);
      setConfigured(!!s.configured);
      setStatusError(s.error ?? "");
      if (!s.configured) return;
      void api.discord_list_channels(bid).then((res) => {
        if (botIdRef.current !== bid) return;
        if (res.ok && res.channels) setChannels(res.channels);
        else setError(res.error ?? "Could not list channels");
      });
    });
  }, [botId]);

  const openChannel = useCallback(
    (channelId: string) => {
      setActiveChannel(channelId);
      setMessages([]);
      setError("");
      setPendingNew(0);
      nearBottomRef.current = true;
      if (!channelId) return;
      const api = getApi();
      if (!api) return;
      setLoading(true);
      void api
        .discord_open_channel(channelId, botIdRef.current)
        .then((res) => {
          if (res.ok && res.messages) setMessages(res.messages);
          else setError(res.error ?? "Could not open channel");
        })
        .finally(() => setLoading(false));
    },
    [],
  );

  // Live messages from the poller — only for the channel + bot currently open.
  useEffect(() => {
    installAgentEventBus();
    return subscribeAgentEvents((event) => {
      if (event.type !== "discord_message" || !event.discord) return;
      if (event.channel_id !== activeChannelRef.current) return;
      if (event.bot_id && event.bot_id !== botIdRef.current) return;
      setMessages((prev) => appendUnique(prev, event.discord!));
    });
  }, []);

  // Server roster for the members column (full variant only).
  useEffect(() => {
    if (variant !== "full" || !status?.configured) {
      setMemberGroups([]);
      setMembersReady(false);
      setMembersError("");
      return;
    }
    let cancelled = false;
    const bid = botId;
    const pull = () => {
      const api = getApi();
      if (!api) return;
      void api.discord_list_members(bid).then((res) => {
        if (cancelled || botIdRef.current !== bid) return;
        if (!res.ok) {
          setMembersReady(false);
          setMembersError(res.error || "Could not load members");
          return;
        }
        if (res.groups) setMemberGroups(res.groups);
        setMembersReady(!!res.ready);
        setMembersError(res.error || "");
      });
    };
    pull();
    const id = window.setInterval(pull, 8000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [variant, status?.configured, botId]);

  const scrollToBottom = useCallback((smooth: boolean) => {
    const el = listRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, []);

  // Autoscroll only while the reader is at the bottom; when they've scrolled up
  // into history, keep their place and count new arrivals in the jump pill.
  useLayoutEffect(() => {
    const channelChanged = prevChannelRef.current !== activeChannel;
    prevChannelRef.current = activeChannel;
    const grew = messages.length - prevCountRef.current;
    prevCountRef.current = messages.length;
    if (channelChanged || nearBottomRef.current) {
      nearBottomRef.current = true;
      scrollToBottom(false);
      setPendingNew(0);
    } else if (grew > 0) {
      setPendingNew((n) => n + grew);
    }
  }, [messages, activeChannel, scrollToBottom]);

  const onListScroll = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 90;
    nearBottomRef.current = nearBottom;
    if (nearBottom) setPendingNew(0);
  }, []);

  const jumpToLatest = useCallback(() => {
    nearBottomRef.current = true;
    setPendingNew(0);
    scrollToBottom(true);
  }, [scrollToBottom]);

  // Composer grows with the draft (up to ~6 lines), then scrolls inside.
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_COMPOSER_HEIGHT_PX)}px`;
  }, [draft, activeChannel]);

  const send = useCallback(() => {
    const text = draft.trim();
    const channel = activeChannelRef.current;
    if (!text || !channel || sending) return;
    const api = getApi();
    if (!api) return;
    setSending(true);
    setError("");
    void api
      .discord_send(channel, text, botIdRef.current)
      .then((res) => {
        if (res.ok) {
          setDraft("");
          nearBottomRef.current = true; // sending always lands you at the latest
          if (res.message) setMessages((prev) => appendUnique(prev, res.message!));
          inputRef.current?.focus();
        } else {
          setError(res.error ?? "Send failed");
        }
      })
      .finally(() => setSending(false));
  }, [draft, sending]);

  const viewMessages = useMemo(() => messages.map(toViewMsg), [messages]);
  const rows = useMemo(() => buildRows(viewMessages), [viewMessages]);
  const channelById = useMemo(() => new Map(channels.map((c) => [c.id, c.name])), [channels]);
  const activeChannelName = activeChannel ? channelById.get(activeChannel) : undefined;

  const connected = !!status?.ok;
  const commandsLocked = connected && !status?.allowed_ids?.trim();
  const prefix = status?.prefix || "!ducky";
  const commands = commandHelp(prefix);

  const switchBot = (next: string) => {
    if (next === botId) return;
    if (variant === "full") {
      const label = bots.find((b) => b.id === next)?.label;
      requestOpenDiscordTab(next, label);
      return;
    }
    setBotId(next);
  };

  const botOptions = bots.map((b) => {
    const state = !b.has_token ? "no token" : !b.enabled ? "disabled" : "ready";
    return {
      value: b.id,
      label: `${b.label || b.id}${b.prefix ? ` · ${b.prefix}` : ""} · ${state}`,
    };
  });

  const botPickerAlways =
    bots.length > 0 ? (
      <div className="groupchat-channel-picker groupchat-bot-picker">
        <ChoiceDropdown
          className="groupchat-channel-select"
          size="compact"
          mode="radio"
          aria-label="Switch Discord bot"
          value={botId}
          options={botOptions}
          onChange={switchBot}
        />
      </div>
    ) : null;

  if (configured === false) {
    return (
      <div className="groupchat-panel groupchat-panel--empty">
        {botPickerAlways}
        <p>This bot isn’t connected.</p>
        <p className="groupchat-hint">
          Open Settings → Discord, select this bot, paste its token and server ID, then Save & Test.
          {statusError ? ` (${statusError})` : ""}
        </p>
        {bots.some((b) => b.id !== botId && b.has_token) ? (
          <p className="groupchat-hint">
            Or pick a ready bot in the dropdown above to switch.
          </p>
        ) : null}
      </div>
    );
  }

  const hubHeader = (
    <details className="groupchat-hub">
      <summary>
        <span className={`discordhub-dot${connected ? " is-ok" : ""}`} aria-hidden />
        <span className="groupchat-hub-name">
          {connected ? `Connected as ${status?.bot_name ?? "bot"}` : "Not connected"}
        </span>
        {bots.length > 1 ? (
          <span
            className="groupchat-hub-bot-picker"
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <ChoiceDropdown
              className="groupchat-channel-select"
              size="compact"
              mode="radio"
              aria-label="Switch Discord bot"
              value={botId}
              options={botOptions}
              onChange={switchBot}
            />
          </span>
        ) : null}
        <span className="groupchat-hub-more">commands</span>
      </summary>
      <div className="discordhub-cmds">
        {commands.map((c) => (
          <div key={c.cmd} className="discordhub-cmd">
            <code className="discordhub-code">{c.cmd}</code>
            <span className="discordhub-cmd-desc">{c.desc}</span>
          </div>
        ))}
      </div>
      {commandsLocked ? (
        <p className="groupchat-hint discordhub-lock">
          ⚠ Commands are locked — add your Discord user ID under Command access in Settings →
          Discord to enable them.
        </p>
      ) : null}
    </details>
  );

  const botPicker = bots.length > 1 ? botPickerAlways : null;

  const channelDropdown = (
    <div className="groupchat-channel-picker">
      <span className="groupchat-channel-picker-hash" aria-hidden>
        #
      </span>
      <ChoiceDropdown
        className="groupchat-channel-select"
        size="compact"
        mode="radio"
        aria-label="Discord channel"
        value={activeChannel}
        placeholder="Select a channel…"
        options={[
          { value: "", label: "Select a channel…" },
          ...channels.map((c) => ({ value: c.id, label: c.name })),
        ]}
        onChange={(next) => openChannel(next)}
      />
    </div>
  );

  const renderMsgRow = (msg: ViewMsg, first: boolean) => {
    const style = { "--nick-h": hashHue(msg.colorKey) } as CSSProperties;
    const jumbo = isEmojiOnly(msg.body);
    return (
      <div
        key={msg.id}
        className={`groupchat-msg${first ? " is-first" : " is-followup"}${msg.bot ? " is-bot" : ""}`}
        style={style}
      >
        <div className="groupchat-gutter-time" aria-hidden>
          {formatTime(msg.ts)}
        </div>
        <div className="groupchat-msg-main">
          <div className={`groupchat-msg-body${jumbo ? " is-jumbo" : ""}`}>
            {first ? (
              <>
                <span className="groupchat-msg-author">&lt;{msg.author}&gt;</span>
                {msg.bot ? <span className="groupchat-tag">BOT</span> : null}
                {msg.relayedBy ? (
                  <span className="groupchat-relay" title={`Sent from Ducky via ${msg.relayedBy}`}>
                    via {msg.relayedBy}
                  </span>
                ) : null}{" "}
              </>
            ) : null}
            {renderInline(msg.body, channelById)}
          </div>
        </div>
      </div>
    );
  };

  const messageList = (
    <div className="groupchat-messages-wrap">
      <div ref={listRef} className="groupchat-messages" onScroll={onListScroll}>
        {loading ? (
          <div className="groupchat-skeletons" aria-hidden>
            {[0, 1, 2].map((i) => (
              <div key={i} className="groupchat-skeleton">
                <div className="groupchat-skeleton-lines">
                  <div className="groupchat-skeleton-line groupchat-skeleton-line--head" />
                  <div className="groupchat-skeleton-line" />
                </div>
              </div>
            ))}
          </div>
        ) : null}
        {!loading && !activeChannel ? (
          <div className="groupchat-empty">
            <span className="groupchat-empty-icon" aria-hidden>
              #
            </span>
            <span>Pick a channel to start chatting.</span>
          </div>
        ) : null}
        {!loading && activeChannel && rows.length === 0 ? (
          <div className="groupchat-empty">
            <span className="groupchat-empty-icon" aria-hidden>
              👋
            </span>
            <span>
              No messages yet — say hi{activeChannelName ? ` in #${activeChannelName}` : ""}.
            </span>
          </div>
        ) : null}
        {rows.map((row) =>
          row.kind === "divider" ? (
            <div key={row.id} className="groupchat-day">
              <span className="groupchat-day-label">{row.label}</span>
            </div>
          ) : (
            renderMsgRow(row.msg, row.first)
          ),
        )}
      </div>
      {pendingNew > 0 ? (
        <button type="button" className="groupchat-jump" onClick={jumpToLatest}>
          ↓ {pendingNew} new message{pendingNew === 1 ? "" : "s"}
        </button>
      ) : null}
    </div>
  );

  const canSend = !!activeChannel && !sending && !!draft.trim();
  const composer = (
    <div className="groupchat-composer">
      <div className={`groupchat-composer-box${activeChannel ? "" : " is-disabled"}`}>
        <textarea
          ref={inputRef}
          className="groupchat-input"
          rows={1}
          placeholder={
            activeChannel
              ? `Message ${activeChannelName ? `#${activeChannelName}` : "channel"}`
              : "Pick a channel first"
          }
          value={draft}
          disabled={!activeChannel || sending}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button
          type="button"
          className={`groupchat-send${sending ? " is-sending" : ""}`}
          disabled={!canSend}
          onClick={send}
          title="Send (Enter — Shift+Enter for a new line)"
          aria-label="Send message"
        >
          <Icons.Send />
        </button>
      </div>
    </div>
  );

  const errorEl = error ? <div className="groupchat-error">{error}</div> : null;

  if (variant === "full") {
    return (
      <div className="groupchat-panel groupchat-panel--full">
        <div className="groupchat-channels">
          <div className="groupchat-col-head">Channels</div>
          {channels.map((c) => (
            <button
              key={c.id}
              type="button"
              className={`groupchat-channel${c.id === activeChannel ? " is-active" : ""}`}
              onClick={() => openChannel(c.id)}
            >
              <span className="groupchat-channel-hash">#</span>
              <span className="groupchat-channel-name">{c.name}</span>
            </button>
          ))}
        </div>
        <div className="groupchat-center">
          {hubHeader}
          {messageList}
          {errorEl}
          {composer}
        </div>
        <div className="groupchat-members">
          <div className="groupchat-col-head">Members</div>
          {memberGroups.length === 0 ? (
            <div className="groupchat-members-empty">
              {membersReady
                ? "No members yet."
                : membersError ||
                  "Loading members… If this stays empty, enable Server Members + Presence intents in the Dev Portal, then restart."}
            </div>
          ) : (
            memberGroups.map((g) => {
              const offline = g.id === "offline";
              const rows = g.members.map((m) => (
                <div
                  key={m.id}
                  className={`groupchat-member${m.bot ? " is-bot" : ""}${offline || m.status === "offline" ? " is-offline" : ""}`}
                  style={memberStyle(m.id, m.color)}
                >
                  <div className="groupchat-avatar-wrap" aria-hidden>
                    <div className="groupchat-avatar groupchat-avatar--sm">{initials(m.name)}</div>
                    <span
                      className={`groupchat-status-dot is-${(m.status || "offline") as DiscordMemberStatus}`}
                    />
                  </div>
                  <span className="groupchat-member-name">{m.name}</span>
                  {m.bot ? <span className="groupchat-tag">BOT</span> : null}
                </div>
              ));
              if (offline) {
                return (
                  <details key={g.id} className="groupchat-member-section groupchat-member-section--offline">
                    <summary className="groupchat-member-section-head">
                      Offline — {g.count}
                    </summary>
                    {rows}
                  </details>
                );
              }
              return (
                <div key={g.id} className="groupchat-member-section">
                  <div className="groupchat-member-section-head">
                    {g.name} — {g.count}
                  </div>
                  {rows}
                </div>
              );
            })
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="groupchat-panel">
      {hubHeader}
      <div className="groupchat-toolbar">
        {botPicker}
        {channelDropdown}
      </div>
      {messageList}
      {errorEl}
      {composer}
    </div>
  );
}
