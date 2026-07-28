import { memo, useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { Icons } from "../../icons/Icons";
import { ChoiceDropdown } from "../../components/ChoiceDropdown";
import { TruncatedText } from "../../components/TruncatedText";
import { ProviderUsageReport } from "../../components/usage/ProviderUsageReport";
import { installPanelPushBus, subscribePanelPush } from "../../hooks/usePanelPushBus";
import type { DiscordStatusDto, PanelPushEvent } from "../../types/panel";
import {
  useFollowCodeSettings,
  type FollowCodeSpeed,
} from "../../verse-editor/queue/followCodeSettings";
import { ProviderCodingAgents } from "./CodingAgentsSection";
import { ProviderIdeHookup } from "./ProviderIdeHookup";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { PluginSettingsSections } from "./PluginSettingsSections";
import { SettingsToggleRow } from "./SettingsToggleRow";
import { DuckyModelPicker } from "../../components/ducky/DuckyModelPicker";
import {
  usePluginContributions,
  type PluginLlmProvider,
} from "../../hooks/usePluginContributions";
import { refreshModelsCatalog } from "../../hooks/modelsCatalogCache";
import { requestOpenSettings } from "../../navigation/openSettingsTab";
import type { SettingsNavLocation } from "../../navigation/settingsHistory";
import {
  useApplySettingsDrill,
  useRecordSettingsLocation,
  useSettingsHistoryBack,
} from "../../navigation/useSettingsHistory";
import {
  requestOpenUsageTab,
  requestUsageSlideOrFocus,
} from "../../navigation/openUsageTab";

const DETAIL_SLIDE_MS = 280;

type KeyStatus = { text: string; ok: boolean; pending?: boolean };

const inFlightTest: { key: string | null; startedAt: number } = { key: null, startedAt: 0 };

function KeyIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.778 7.778 5.5 5.5 0 017.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
    </svg>
  );
}

function DatabaseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
    </svg>
  );
}

function EyeIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
      <path d="M7 11V7a5 5 0 0110 0v4" />
    </svg>
  );
}

function applyKeyTestResult(
  key: string,
  ok: boolean,
  detail: string,
  setters: {
    setKeySaved: Dispatch<SetStateAction<Record<string, boolean>>>;
    setDraftKeys: Dispatch<SetStateAction<Record<string, string>>>;
    setKeyStatus: Dispatch<SetStateAction<Record<string, KeyStatus>>>;
    setTestingKey: Dispatch<SetStateAction<string | null>>;
  },
) {
  if (inFlightTest.key === key) inFlightTest.key = null;
  setters.setTestingKey(null);
  if (ok) {
    setters.setKeySaved((s) => ({ ...s, [key]: true }));
    setters.setDraftKeys((d) => ({ ...d, [key]: "" }));
    setters.setKeyStatus((s) => ({ ...s, [key]: { text: "Saved", ok: true } }));
    window.setTimeout(() => {
      setters.setKeyStatus((s) => {
        const entry = s[key];
        if (entry?.ok && entry.text === "Saved") {
          const next = { ...s };
          delete next[key];
          return next;
        }
        return s;
      });
    }, 4000);
    return;
  }
  setters.setKeyStatus((s) => ({
    ...s,
    [key]: { text: detail || "Test failed", ok: false },
  }));
}

function DefaultModelSection() {
  const [loaded, setLoaded] = useState(false);
  const [defaultModel, setDefaultModel] = useState("");
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");

  useEffect(() => {
    return onApiReady((api) => {
      void api.get_settings().then((settings) => {
        setDefaultModel((settings.default_model || "").trim());
        setLoaded(true);
      });
    });
  }, []);

  const save = async (next: string) => {
    const api = getApi();
    if (!api) return;
    setDefaultModel(next);
    setSaveStatus("saving");
    try {
      await api.save_agent_settings({ default_model: next });
      setSaveStatus("saved");
      window.setTimeout(() => setSaveStatus((s) => (s === "saved" ? "idle" : s)), 2500);
    } catch {
      setSaveStatus("error");
    }
  };

  return (
    <div className="llms-default-model-card">
      <div className="llms-default-model-row">
        <h3 className="llms-default-model-title">
          <span className="general-tab-section-icon" aria-hidden>
            <Icons.Brain />
          </span>
          Default Model
        </h3>
        <div className="llms-default-model-picker">
          <DuckyModelPicker
            model={defaultModel}
            onChange={(model) => void save(model)}
            allowClear
            placeholder={loaded ? "No models" : "Loading…"}
            label=""
            hint=""
            menuPlacement="bottom"
          />
        </div>
      </div>
      <p className="llms-default-model-note">
        Duckies and chats without their own model use this.
        {saveStatus === "saving"
          ? " Saving…"
          : saveStatus === "saved"
            ? " Saved."
            : saveStatus === "error"
              ? " Could not save."
              : ""}
      </p>
    </div>
  );
}

/** Ducky-owned freeze — works for every gateway. Provider cache markers live in plugins. */
function PromptPrefixSection() {
  const [loaded, setLoaded] = useState(false);
  const [freezePrefix, setFreezePrefix] = useState(true);

  useEffect(() => {
    return onApiReady((api) => {
      void api.get_settings().then((settings) => {
        if (typeof settings.freeze_prompt_prefix === "boolean") {
          setFreezePrefix(settings.freeze_prompt_prefix);
        }
        setLoaded(true);
      });
    });
  }, []);

  return (
    <div className="general-tab-toggle-card">
      <SettingsToggleRow
        id="toggle-cache-freeze"
        label="Freeze prompt prefix per chat"
        checked={freezePrefix}
        disabled={!loaded}
        onChange={(value) => {
          setFreezePrefix(value);
          const api = getApi();
          if (api) void api.save_agent_settings({ freeze_prompt_prefix: value });
        }}
      />
    </div>
  );
}

export const DiscordSetupGuide = memo(function DiscordSetupGuide({
  openPortal,
}: {
  openPortal: () => void;
}) {
  return (
    <ol className="discord-setup-checklist">
      <li>
        <span className="discord-setup-step-n">1</span>
        <span>
          <button type="button" className="discord-setup-link" onClick={openPortal}>
            Open Developer Portal
          </button>
          {" → New Application → name it"}
        </span>
      </li>
      <li>
        <span className="discord-setup-step-n">2</span>
        <span>Bot tab → Reset Token → copy → paste into <strong>Bot token</strong> below</span>
      </li>
      <li>
        <span className="discord-setup-step-n">3</span>
        <span>
          Same page → turn ON Message Content, Server Members, Presence intents → Save
        </span>
      </li>
      <li>
        <span className="discord-setup-step-n">4</span>
        <span>
          OAuth2 → URL Generator → scope <code>bot</code> + Send Messages → invite to your server
        </span>
      </li>
      <li>
        <span className="discord-setup-step-n">5</span>
        <span>
          Discord Developer Mode ON → right-click server → Copy Server ID → paste below →{" "}
          <strong>Save & Test</strong>
        </span>
      </li>
    </ol>
  );
});

export function DiscordSection() {
  const [token, setToken] = useState("");
  const [guild, setGuild] = useState("");
  const [postName, setPostName] = useState("");
  const [allowedIds, setAllowedIds] = useState("");
  const [status, setStatus] = useState<DiscordStatusDto | null>(null);
  const [busy, setBusy] = useState(false);
  const connectedAs = "Connected as";
  const notReachable = "Not reachable";
  const notConnected = "Not connected";
  const phToken = "Discord bot token";
  const phServer = "Right-click your server → Copy Server ID";
  const phPostAs = "Optional — your name on messages; blank posts as the bot";
  const phAccess = "Your Discord user ID (comma-separated for a team; * = anyone)";
  const lockedMsg =
    "⚠ !ducky commands are locked — add your Discord user ID below to enable them for yourself. Others in the channel can’t run your duckies until you list them.";
  const editBotLabel = "Edit bot name & avatar ↗";

  useEffect(() => {
    return onApiReady((api) => {
      void api.discord_status().then((s) => {
        setStatus(s);
        if (s.guild_id) setGuild(s.guild_id);
        if (s.post_name) setPostName(s.post_name);
        if (s.allowed_ids) setAllowedIds(s.allowed_ids);
      });
    });
  }, []);

  const saveAndTest = async () => {
    const api = getApi();
    if (!api) return;
    setBusy(true);
    const keys: Record<string, string> = {};
    if (token.trim()) keys.discord = token.trim();
    if (guild.trim()) keys.discord_guild = guild.trim();
    // discord_name / discord_allowed_ids go top-level (not keys) so clearing them clears the value.
    await api.save_agent_settings({
      keys,
      discord_name: postName.trim(),
      discord_allowed_ids: allowedIds.trim(),
    });
    setToken("");
    setStatus(await api.discord_status());
    setBusy(false);
  };

  const [diag, setDiag] = useState<Awaited<
    ReturnType<NonNullable<ReturnType<typeof getApi>>["discord_debug"]>
  > | null>(null);
  const runDiag = async () => {
    const api = getApi();
    if (api) setDiag(await api.discord_debug());
  };

  const commandsLocked = status?.ok ? !allowedIds.trim() : false;

  const statusLine = status
    ? status.ok
      ? { text: `${connectedAs} ${status.bot_name ?? "bot"}`, ok: true }
      : status.configured
        ? { text: status.error ?? notReachable, ok: false }
        : { text: notConnected, ok: false }
    : null;

  const openPortal = useCallback(() => {
    const api = getApi();
    if (api) void api.open_external_url("https://discord.com/developers/applications");
  }, []);

  return (
    <div className="llms-provider-card">
      <details className="discord-setup-guide" open={!status?.configured}>
        <summary>
          How to set up your bot (one time, ~3 minutes)
        </summary>
        <DiscordSetupGuide openPortal={openPortal} />
      </details>
      <div className="llms-provider-row">
        <div className="llms-provider-row-main">
          <div className={`llms-provider-label${status?.ok ? " is-saved" : ""}`}>
            {status?.ok ? <Icons.Check /> : <span className="llms-provider-dot" title={notConnected} />}
            <span>
              Bot token
            </span>
          </div>
          <input
            className="settings-input llms-provider-input"
            type="password"
            value={token}
            placeholder={status?.configured ? "••••••••••••••••" : phToken}
            onChange={(e) => setToken(e.target.value)}
          />
        </div>
        <div className="llms-provider-row-main">
          <div className="llms-provider-label">
            <span>
              Server ID
            </span>
          </div>
          <input
            className="settings-input llms-provider-input"
            type="text"
            value={guild}
            placeholder={phServer}
            onChange={(e) => setGuild(e.target.value)}
          />
        </div>
        <div className="llms-provider-row-main">
          <div className="llms-provider-label">
            <span>
              Post as
            </span>
          </div>
          <input
            className="settings-input llms-provider-input"
            type="text"
            value={postName}
            placeholder={phPostAs}
            onChange={(e) => setPostName(e.target.value)}
          />
        </div>
        <div className="llms-provider-row-main">
          <div className="llms-provider-label">
            <span>
              !ducky access
            </span>
          </div>
          <input
            className="settings-input llms-provider-input"
            type="text"
            value={allowedIds}
            placeholder={phAccess}
            onChange={(e) => setAllowedIds(e.target.value)}
          />
          <button
            type="button"
            className="settings-btn llms-provider-btn"
            disabled={busy}
            onClick={() => void saveAndTest()}
          >
            {busy ? "Testing…" : "Save & Test"}
          </button>
        </div>
        {commandsLocked ? (
          <div className="llms-provider-status is-fail">
            <TruncatedText title={lockedMsg} className="llms-provider-status-text">
              {lockedMsg}
            </TruncatedText>
          </div>
        ) : null}
        <details className="discord-setup-guide" open={commandsLocked}>
          <summary>
            How do I find my Discord user ID?
          </summary>
          <ol className="discord-setup-steps">
            <li>
              In Discord: Settings → Advanced → Developer Mode → turn ON.
            </li>
            <li>
              Right-click your own name (on any message, or in the member list) → Copy User ID.
            </li>
            <li>
              Paste that number into !ducky access above and hit Save & Test. It’s a long number
                like your Server ID — not your username or “Post as” name.
            </li>
            <li>
              Easiest of all: type !ducky whoami in your Discord channel and the bot replies with
                your ID to paste here. (Open that channel in the Discord panel first so the bot is
                watching it.)
            </li>
          </ol>
        </details>
        {statusLine ? (
          <div className={`llms-provider-status ${statusLine.ok ? "is-ok" : "is-fail"}`}>
            {statusLine.ok ? <Icons.Check /> : null}
            <TruncatedText title={statusLine.text} className="llms-provider-status-text">
              {statusLine.text}
            </TruncatedText>
            {status?.ok ? (
              <button
                type="button"
                className="discord-setup-link"
                title={editBotLabel}
                onClick={() => {
                  const api = getApi();
                  if (api) void api.discord_open_portal();
                }}
              >
                {editBotLabel}
              </button>
            ) : null}
          </div>
        ) : null}

        {status?.ok ? (
          <div className="llms-provider-row-main">
            <div className="llms-provider-label">
              <span>
                Commands
              </span>
            </div>
            <button type="button" className="settings-btn" onClick={() => void runDiag()}>
              Diagnose !ducky
            </button>
          </div>
        ) : null}
        {diag ? (
          <>
            <div className={`llms-provider-status ${diag.poller_alive ? "is-ok" : "is-fail"}`}>
              <TruncatedText
                title={JSON.stringify(diag)}
                className="llms-provider-status-text"
              >
                {diag.poller_alive
                  ? `Watching #${diag.watching_channel_name || diag.watching_channel_id || "(none)"} · ${
                      diag.last_seen
                        ? `last saw “${diag.last_seen.preview ?? ""}” from ${diag.last_seen.author} (len ${diag.last_seen.content_len})`
                        : "no messages seen yet — type something in that channel"
                    }`
                  : "Poller not running — hit Save & Test, or restart the app."}
              </TruncatedText>
            </div>
            {!diag.watching_channel_id ? (
              <div className="llms-provider-status is-fail">
                <TruncatedText title="Open the channel in the Discord panel first" className="llms-provider-status-text">
                  ⚠ No channel is being watched — open a channel in the Discord panel, then type
                  !ducky in that same channel.
                </TruncatedText>
              </div>
            ) : null}
            {diag.last_seen && diag.last_seen.content_len === 0 ? (
              <div className="llms-provider-status is-fail">
                <TruncatedText title="Message Content Intent is required" className="llms-provider-status-text">
                  ⚠ Messages read as blank — turn ON Message Content Intent in the Dev Portal (Bot
                  tab), then Save.
                </TruncatedText>
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}

const FOLLOW_CODE_SPEEDS: { value: FollowCodeSpeed; label: string }[] = [
  { value: "slow", label: "Slow" },
  { value: "normal", label: "Normal" },
  { value: "fast", label: "Fast" },
  { value: "instant", label: "Instant (no highlights)" },
];

function FollowCodeSection() {
  const { settings, update } = useFollowCodeSettings();

  return (
    <div className="general-tab-toggle-card">
      <SettingsToggleRow
        id="toggle-follow-edits"
        label="Follow agent edits in the editor"
        checked={settings.enabled}
        onChange={(checked) => update({ enabled: checked })}
      />
      <SettingsToggleRow
        id="toggle-follow-tabs"
        label="Open files in a tab group beside the chat"
        checked={settings.splitBesideChat}
        disabled={!settings.enabled}
        onChange={(checked) => update({ splitBesideChat: checked })}
      />
      <div className="general-tab-toggle-row">
        <div className="general-tab-toggle-row-text">
          <label htmlFor="select-walkthrough" className="general-tab-toggle-label">
            Walkthrough speed
          </label>
        </div>
        <ChoiceDropdown
          id="select-walkthrough"
          className="llms-speed-select"
          size="compact"
          aria-label="Walkthrough speed"
          mode="radio"
          value={settings.speed}
          disabled={!settings.enabled}
          options={FOLLOW_CODE_SPEEDS.map((s) => ({ value: s.value, label: s.label }))}
          onChange={(next) => update({ speed: next as FollowCodeSpeed })}
        />
      </div>
    </div>
  );
}

export function AgentTab() {
  const contrib = usePluginContributions();
  const [keySaved, setKeySaved] = useState<Record<string, boolean>>({});
  const [draftKeys, setDraftKeys] = useState<Record<string, string>>({});
  const [keyStatus, setKeyStatus] = useState<Record<string, KeyStatus>>({});
  const [testingKey, setTestingKey] = useState<string | null>(() => inFlightTest.key);
  const [testElapsed, setTestElapsed] = useState(0);
  const [usageTarget, setUsageTarget] = useState<{ id: string; label: string } | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const providers = contrib.llm_providers;
  const selected = useMemo(
    () => providers.find((p) => p.id === selectedId) ?? null,
    [providers, selectedId],
  );
  const detailOpen = selected !== null;
  const usageOpen = usageTarget !== null;
  const [detailRendered, setDetailRendered] = useState(detailOpen);
  const [usageRendered, setUsageRendered] = useState(usageOpen);
  useEffect(() => {
    if (detailOpen) {
      setDetailRendered(true);
      return;
    }
    const timer = window.setTimeout(() => setDetailRendered(false), DETAIL_SLIDE_MS);
    return () => window.clearTimeout(timer);
  }, [detailOpen]);
  useEffect(() => {
    if (usageOpen) {
      setUsageRendered(true);
      return;
    }
    const timer = window.setTimeout(() => setUsageRendered(false), DETAIL_SLIDE_MS);
    return () => window.clearTimeout(timer);
  }, [usageOpen]);

  // Drop selection if the Store plugin was disabled/uninstalled.
  useEffect(() => {
    if (selectedId && !providers.some((p) => p.id === selectedId)) {
      setSelectedId(null);
      setUsageTarget(null);
    }
  }, [providers, selectedId]);

  const llmsNavLoc = useMemo<SettingsNavLocation>(() => {
    if (!selectedId) {
      return { kind: "settings", tab: "LLMs", sectionTab: "llms", name: "LLMs" };
    }
    const label = providers.find((p) => p.id === selectedId)?.label || selectedId;
    return {
      kind: "settings",
      tab: "LLMs",
      sectionTab: "llms",
      drill: {
        type: "llms",
        providerId: selectedId,
        usage: Boolean(usageTarget),
      },
      name: usageTarget ? `${label} usage` : label,
    };
  }, [selectedId, usageTarget, providers]);
  useRecordSettingsLocation(llmsNavLoc);

  const applyLlmsDrill = useCallback(
    (loc: SettingsNavLocation) => {
      if (loc.sectionTab && loc.sectionTab !== "llms") return;
      const drill = loc.drill?.type === "llms" ? loc.drill : null;
      if (!drill?.providerId) {
        setSelectedId(null);
        setUsageTarget(null);
        return;
      }
      const provider = providers.find((p) => p.id === drill.providerId);
      if (!provider) {
        setSelectedId(null);
        setUsageTarget(null);
        return;
      }
      setSelectedId(provider.id);
      if (drill.usage) {
        setUsageTarget({ id: provider.id, label: provider.label });
      } else {
        setUsageTarget(null);
      }
    },
    [providers],
  );
  useApplySettingsDrill("LLMs", applyLlmsDrill);

  const backFromDetail = useSettingsHistoryBack(() => {
    setUsageTarget(null);
    setSelectedId(null);
  });
  const backFromUsage = useSettingsHistoryBack(() => setUsageTarget(null));

  const handleKeyTestPush = useCallback((event: PanelPushEvent) => {
    if (!event.provider) return;
    if (event.type === "key_test_progress") {
      setTestingKey(event.provider);
      setKeyStatus((s) => ({
        ...s,
        [event.provider!]: { text: event.detail ?? "Testing…", ok: true, pending: true },
      }));
      return;
    }
    if (event.type !== "key_test_done") return;
    applyKeyTestResult(event.provider, !!event.ok, event.detail ?? "", {
      setKeySaved,
      setDraftKeys,
      setKeyStatus,
      setTestingKey,
    });
  }, []);

  useEffect(() => {
    if (!testingKey) {
      setTestElapsed(0);
      return;
    }
    const tick = () =>
      setTestElapsed(Math.max(0, Math.floor((Date.now() - inFlightTest.startedAt) / 1000)));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [testingKey]);

  useEffect(() => {
    return onApiReady((api) => {
      void api.get_key_status().then(setKeySaved);
    });
  }, []);

  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      handleKeyTestPush(event);
      if (event.type !== "uefn_plugins_changed") return;
      void refreshModelsCatalog();
      const api = getApi();
      if (api) void api.get_key_status().then(setKeySaved);
    });
  }, [handleKeyTestPush]);

  const testKey = async (row: PluginLlmProvider) => {
    const api = getApi();
    if (!api) return;
    const key = row.secret_key || row.id;
    const value = draftKeys[key];
    const isUrl = row.kind === "url";
    if (!isUrl && !value?.trim()) return;

    setKeyStatus((s) => ({
      ...s,
      [key]: {
        text: isUrl
          ? "Testing — first run may load the model, this can take a few minutes…"
          : "Testing…",
        ok: true,
        pending: true,
      },
    }));
    inFlightTest.key = key;
    inFlightTest.startedAt = Date.now();
    setTestingKey(key);

    try {
      const { runBridgeJob } = await import("../../hooks/bridgeJobAsync");
      const res = await runBridgeJob<{ ok: boolean; detail: string }>(
        "test_key",
        [key, (value ?? "").trim()],
        300_000,
      );
      applyKeyTestResult(key, res.ok, res.detail, {
        setKeySaved,
        setDraftKeys,
        setKeyStatus,
        setTestingKey,
      });
      if (res.ok) {
        const toSave = isUrl
          ? (value ?? "").trim() || (row.default_url || "").trim()
          : (value ?? "").trim();
        if (toSave) void api.save_agent_settings({ keys: { [key]: toSave } });
      }
    } catch (e) {
      if (inFlightTest.key === key) inFlightTest.key = null;
      setTestingKey(null);
      setKeyStatus((s) => ({
        ...s,
        [key]: { text: e instanceof Error ? e.message : "Test failed", ok: false },
      }));
    }
  };

  const secretKey = selected ? selected.secret_key || selected.id : "";
  const isUrl = selected?.kind === "url";
  const saved = secretKey ? !!keySaved[secretKey] : false;
  const status = secretKey ? keyStatus[secretKey] : undefined;
  const testing = !!secretKey && testingKey === secretKey;
  const draftValue = secretKey ? (draftKeys[secretKey] ?? "") : "";
  const justSaved = saved && !testing && !status && !draftValue.trim();
  const canTest = !!selected && (isUrl || !!draftValue.trim());

  return (
    <div className="llms-tab">
      <div
        className={`duckies-tab-body${detailOpen ? " is-detail" : ""}${usageOpen ? " is-usage" : ""}`}
      >
        <div className="duckies-tab-list-shell">
          <div className="llms-tab-list" aria-label="LLM settings">
            <h2 className="general-tab-page-title">LLMs</h2>

            <DefaultModelSection />

            <section className="general-tab-section">
              <GeneralSectionHeader icon={<KeyIcon />} title="Providers" />
              <div className="llms-provider-card">
                {providers.map((row) => {
                  const key = row.secret_key || row.id;
                  const rowSaved = !!keySaved[key];
                  return (
                    <button
                      key={row.id}
                      type="button"
                      className="llms-provider-nav-row"
                      onClick={() => setSelectedId(row.id)}
                    >
                      <span className={`llms-provider-label${rowSaved ? " is-saved" : ""}`}>
                        {rowSaved ? (
                          <Icons.Check />
                        ) : (
                          <span className="llms-provider-dot" title="No key saved" />
                        )}
                        <span className="llms-provider-nav-name">{row.label}</span>
                      </span>
                      <span className="llms-provider-nav-chevron" aria-hidden>
                        <Icons.ChevronRight />
                      </span>
                    </button>
                  );
                })}
                {contrib.ready && providers.length === 0 ? (
                  <div className="llms-provider-empty">
                    <p className="general-tab-section-desc">
                      Install a gateway from the Store to add providers (OpenAI, Anthropic, Cursor, …).
                    </p>
                    <button
                      type="button"
                      className="settings-btn"
                      onClick={() => {
                        try {
                          sessionStorage.setItem("uefn-store-category", "gateways");
                        } catch {
                          /* ignore */
                        }
                        requestOpenSettings("Store");
                        window.dispatchEvent(new CustomEvent("ducky:store-navigate"));
                      }}
                    >
                      Open Plugins
                    </button>
                  </div>
                ) : null}
              </div>
            </section>

            <section className="general-tab-section">
              <GeneralSectionHeader icon={<DatabaseIcon />} title="Prompt prefix" />
              <PromptPrefixSection />
            </section>

            <section className="general-tab-section">
              <GeneralSectionHeader icon={<EyeIcon />} title="Follow Code" />
              <FollowCodeSection />
            </section>

            <section className="general-tab-footer-card">
              <GeneralSectionHeader icon={<LockIcon />} title="Key Storage" />
              <button
                type="button"
                className="settings-btn"
                onClick={() => {
                  const api = getApi();
                  if (api) void api.open_appdata();
                }}
              >
                Open folder
              </button>
            </section>
          </div>
        </div>

        <div className="duckies-tab-detail" aria-hidden={!detailOpen}>
          {detailRendered && selected ? (
            <div className="duckies-tab-detail-inner">
              <header className="duckies-tab-detail-head">
                <div className="duckies-tab-detail-head-left">
                  <button
                    type="button"
                    className="duckies-tab-detail-back"
                    onClick={backFromDetail}
                    aria-label="Back to LLM settings"
                  >
                    <span className="duckies-tab-detail-back-icon" aria-hidden>
                      <Icons.ChevronRight />
                    </span>
                  </button>
                  <span className="duckies-tab-detail-divider" aria-hidden />
                  <h3 className="duckies-tab-detail-title">{selected.label}</h3>
                </div>
                <div className="duckies-tab-detail-head-actions">
                  <button
                    type="button"
                    className="settings-btn llms-provider-stats-btn"
                    title={`${selected.label} usage (last 7 days)`}
                    aria-label={`${selected.label} usage report`}
                    onClick={() => {
                      const id = secretKey;
                      const label = selected.label;
                      void requestUsageSlideOrFocus({ providerId: id, label }, () =>
                        setUsageTarget({ id, label }),
                      );
                    }}
                  >
                    <Icons.Chart />
                  </button>
                </div>
              </header>

              <div className="duckies-tab-detail-scroll">
                {/* API key / URL only when this Store plugin contributes an llm.providers row. */}
                {selected.secret_key || selected.kind === "url" ? (
                  <section className="general-tab-section">
                    <GeneralSectionHeader
                      icon={<KeyIcon />}
                      title={isUrl ? "Server URL" : "API key"}
                    />
                    <div className="llms-provider-card">
                      <div className="llms-provider-row">
                        <div className="llms-provider-row-main">
                          <div className={`llms-provider-label${saved ? " is-saved" : ""}`}>
                            {saved ? (
                              <Icons.Check />
                            ) : (
                              <span className="llms-provider-dot" title="No key saved" />
                            )}
                            <span>{selected.label}</span>
                          </div>
                          <input
                            className="settings-input llms-provider-input"
                            type={isUrl ? "text" : "password"}
                            value={draftValue}
                            placeholder={
                              isUrl
                                ? saved
                                  ? "Saved — leave blank to keep"
                                  : selected.default_url || "http://localhost:…"
                                : saved
                                  ? "••••••••••••••••"
                                  : ""
                            }
                            onChange={(e) =>
                              setDraftKeys({ ...draftKeys, [secretKey]: e.target.value })
                            }
                          />
                          <button
                            type="button"
                            className={`settings-btn llms-provider-btn${justSaved ? " is-saved" : ""}`}
                            disabled={testing || !canTest}
                            onClick={() => void testKey(selected)}
                          >
                            {testing ? (
                              `Testing… ${testElapsed}s`
                            ) : justSaved ? (
                              <>
                                <Icons.Check />
                                Saved
                              </>
                            ) : (
                              "Test & Save"
                            )}
                          </button>
                        </div>
                        {status ? (
                          <div
                            className={`llms-provider-status ${
                              status.pending ? "is-pending" : status.ok ? "is-ok" : "is-fail"
                            }`}
                          >
                            {status.ok && !status.pending ? <Icons.Check /> : null}
                            <TruncatedText title={status.text} className="llms-provider-status-text">
                              {status.text}
                            </TruncatedText>
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </section>
                ) : null}

                <ProviderIdeHookup pluginId={selected.plugin_id} />

                <ProviderCodingAgents pluginId={selected.plugin_id} />

                <PluginSettingsSections pluginId={selected.plugin_id} compact />
              </div>
            </div>
          ) : null}
        </div>

        <div className="duckies-tab-usage" aria-hidden={!usageOpen}>
          {usageRendered && usageTarget ? (
            <div className="duckies-tab-detail-inner">
              <header className="duckies-tab-detail-head">
                <div className="duckies-tab-detail-head-left">
                  <button
                    type="button"
                    className="duckies-tab-detail-back"
                    onClick={backFromUsage}
                    aria-label="Back to provider"
                  >
                    <span className="duckies-tab-detail-back-icon" aria-hidden>
                      <Icons.ChevronRight />
                    </span>
                  </button>
                  <span className="duckies-tab-detail-divider" aria-hidden />
                  <h3 className="duckies-tab-detail-title">{usageTarget.label} usage</h3>
                </div>
                <div className="duckies-tab-detail-head-actions">
                  <button
                    type="button"
                    className="settings-btn"
                    title="Open as tab"
                    aria-label="Open as tab"
                    onClick={() => {
                      requestOpenUsageTab({
                        providerId: usageTarget.id,
                        label: usageTarget.label,
                      });
                      setUsageTarget(null);
                    }}
                  >
                    <Icons.Split />
                  </button>
                </div>
              </header>
              <div className="duckies-tab-detail-scroll">
                <ProviderUsageReport
                  providerId={usageTarget.id}
                  label={usageTarget.label}
                  days={7}
                  hideTitle
                />
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
