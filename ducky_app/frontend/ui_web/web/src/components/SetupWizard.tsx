/**
 * First-run setup: Welcome, the starter plugins (one Install, a tile per plugin with its Store
 * icon and progress), connecting an AI (an API key, or a plan you already pay for), then the
 * defaults new chats start with. Short labels on screen; the long version sits behind a "?".
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { getApi } from "../hooks/usePanelApi";
import { usePluginContributions, type PluginLlmProvider } from "../hooks/usePluginContributions";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import { peekStoreCatalogCache, rememberStoreCatalog } from "../hooks/storeCatalogCache";
import { refreshModelsCatalog } from "../hooks/modelsCatalogCache";
import { usageTrack } from "../hooks/usageTrack";
import { Icons } from "../icons/Icons";
import type { CodingAgentDto, DuckyOSAccountStatus, DuckyOSStoreItemDto, StarterPluginDto } from "../types/panel";
import { CodingAgentLoginModal } from "../views/settings/CodingAgentsSection";
import { DuckyModelPicker } from "./ducky/DuckyModelPicker";
import { MediaMakerPicker, type MediaKind } from "./MediaMakerPicker";
import { loadVoiceSettings, saveVoiceSettings } from "../voice/voiceSettings";

export type SetupStep = "welcome" | "plugins" | "ai" | "defaults";
export type InstallPhase = "ready" | "installing" | "installed" | "error";
export type InstallRow = { phase: InstallPhase; detail?: string };

const STEPS: Array<{ id: SetupStep; label: string }> = [
  { id: "welcome", label: "Welcome" },
  { id: "plugins", label: "Plugins" },
  { id: "ai", label: "Connect AI" },
  { id: "defaults", label: "Defaults" },
];

export interface SetupWizardProps {
  step: SetupStep;
  onStep: (step: SetupStep) => void;
  plugins: StarterPluginDto[];
  rows: Record<string, InstallRow>;
  installing: boolean;
  installError: string;
  onInstall: () => void;
  onFinish: () => void;
  /** Only when opened again later (Settings, the corner card): the first run can't be closed. */
  onClose?: () => void;
}

/** A "?" that shows the long version of a short label on hover, focus or press. */
export function HelpTip({ text, label }: { text: string; label: string }) {
  const id = useId();
  const btn = useRef<HTMLButtonElement>(null);
  const [at, setAt] = useState<{ x: number; y: number; below: boolean } | null>(null);
  const show = () => {
    const r = btn.current?.getBoundingClientRect();
    if (!r) return;
    const half = 140; // half the popover's max width, so it stays on screen
    const x = Math.max(half + 8, Math.min(r.left + r.width / 2, window.innerWidth - half - 8));
    const below = r.top < 160;
    setAt({ x, y: below ? r.bottom + 8 : r.top - 8, below });
  };
  const hide = () => setAt(null);
  return (
    <>
      <button
        ref={btn}
        type="button"
        className="setup-tip"
        aria-label={label}
        aria-describedby={at ? id : undefined}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onClick={(e) => {
          e.stopPropagation();
          if (at) hide();
          else show();
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape" && at) {
            e.stopPropagation();
            hide();
          }
        }}
      >
        ?
      </button>
      {at
        ? createPortal(
            <span
              ref={(el) => {
                el?.style.setProperty("--tip-x", `${at.x}px`);
                el?.style.setProperty("--tip-y", `${at.y}px`);
              }}
              role="tooltip"
              id={id}
              className={`setup-tip-pop${at.below ? " is-below" : ""}`}
            >
              {text}
            </span>,
            document.body,
          )
        : null}
    </>
  );
}

/** Store name, description and icon per slug: the cached catalog first, then a fresh read. */
function useStoreItems(): Record<string, DuckyOSStoreItemDto> {
  const index = (items: DuckyOSStoreItemDto[] | undefined) =>
    Object.fromEntries((items || []).filter((i) => i.slug).map((i) => [String(i.slug).toLowerCase(), i]));
  const [items, setItems] = useState(() => index(peekStoreCatalogCache()?.items));
  useEffect(() => {
    let alive = true;
    let timer = 0;
    const api = getApi();
    if (!api || typeof api.duckyos_store_catalog !== "function") return;
    const fetchCatalog = api.duckyos_store_catalog.bind(api);
    // The catalog carries every icon: a slow link can time it out, so try again a few times.
    const load = (attempt: number): void => {
      void Promise.resolve(fetchCatalog())
        .then((next) => {
          if (!alive) return;
          if (!next || next.ok === false || !next.items?.length) throw new Error("no catalog");
          rememberStoreCatalog(next);
          setItems(index(next.items));
        })
        .catch(() => {
          if (alive && attempt < 3) timer = window.setTimeout(() => load(attempt + 1), 3000 * 2 ** attempt);
        });
    };
    load(0);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);
  return items;
}

function StepDots({ step }: { step: SetupStep }) {
  const at = STEPS.findIndex((s) => s.id === step);
  return (
    <ol className="setup-wizard-steps" aria-label="Setup steps">
      {STEPS.map((s, i) => (
        <li
          key={s.id}
          className={`setup-wizard-step${i < at ? " is-done" : ""}${i === at ? " is-current" : ""}`}
          aria-current={i === at ? "step" : undefined}
        >
          <span className="setup-wizard-step-num" aria-hidden>
            {i < at ? <Icons.Check /> : i + 1}
          </span>
          <span className="setup-wizard-step-label">{s.label}</span>
        </li>
      ))}
    </ol>
  );
}

function Feature({ icon, tone, title, body, tip }: { icon: ReactNode; tone: string; title: string; body: string; tip: string }) {
  return (
    <li className="setup-wizard-feature">
      <span className={`setup-wizard-chip is-${tone}`} aria-hidden>
        {icon}
      </span>
      <strong>
        {title} <HelpTip label={`About ${title}`} text={tip} />
      </strong>
      <span className="setup-wizard-muted">{body}</span>
    </li>
  );
}

function WelcomeStep() {
  return (
    <div className="setup-wizard-hero">
      <img className="setup-wizard-logo" src="./OnlineMCPIcon.png" alt="" draggable={false} />
      <h2 id="setup-wizard-title">Welcome to UEFN Ducky</h2>
      <p className="setup-wizard-lead">Your AI crew for Fortnite islands.</p>
      <ul className="setup-wizard-features">
        <Feature
          icon={<Icons.Duck />}
          tone="amber"
          title="Duckies"
          body="AI helpers you chat with"
          tip="Each ducky knows your island and can use UEFN's tools: it writes Verse, builds levels, and makes materials and VFX for you."
        />
        <Feature
          icon={<Icons.Puzzle />}
          tone="purple"
          title="Plugins"
          body="What duckies can do"
          tip="Verse, level design, materials, VFX, 3D, play tests and more, from the UEFN Ducky Store."
        />
        <Feature
          icon={<Icons.Brain />}
          tone="blue"
          title="Your AI"
          body="Your key or your plan"
          tip="Connect Anthropic, OpenAI, Google or Cursor with an API key, or sign in to a plan you already pay for."
        />
      </ul>
      <p className="setup-wizard-privacy">
        Anonymous usage counts only{" "}
        <HelpTip
          label="About usage counts"
          text="Ducky sends anonymous usage counts (which features, which errors by code, how long it was open) under a random install id. Never your chats, files or project names. One switch in Settings → Permissions turns it off."
        />
      </p>
    </div>
  );
}

const GROUPS: Array<{ id: StarterPluginDto["group"]; title: string; tip: string }> = [
  { id: "ai", title: "AI models", tip: "Gateways connect Ducky to AI models. You connect one in the next step." },
  { id: "uefn", title: "UEFN", tip: "Work the UEFN editor, play test your island in Fortnite, and judge automations." },
  { id: "create", title: "Art & 3D", tip: "3D models, pictures, Adobe apps, and trailers and artwork for your island." },
  { id: "extras", title: "Extras", tip: "A web browser inside Ducky, translation for the app, and offline voices that read replies aloud." },
];

function StatusIcon({ row, name }: { row: InstallRow | undefined; name: string }) {
  const phase = row?.phase ?? "ready";
  if (phase === "installing") {
    return (
      <span className="setup-wizard-status is-installing" aria-label={`${name}: Installing`}>
        <Icons.Spinner />
      </span>
    );
  }
  if (phase === "installed") {
    return (
      <span className="setup-wizard-status is-installed" aria-label={`${name}: Installed`}>
        <Icons.Check />
      </span>
    );
  }
  if (phase === "error") {
    return (
      <span className="setup-wizard-status is-error" aria-label={`${name}: Failed`}>
        <Icons.AlertTriangle />
      </span>
    );
  }
  return null;
}

function PluginTile({
  plugin,
  row,
  item,
}: {
  plugin: StarterPluginDto;
  row: InstallRow | undefined;
  item: DuckyOSStoreItemDto | undefined;
}) {
  const icon = item?.icon_data_url;
  const name = item?.name || plugin.label;
  const desc = (item?.description || "").trim();
  return (
    <li className={`setup-wizard-plugin is-${row?.phase ?? "ready"}`}>
      <span className="setup-wizard-plugin-icon" aria-hidden>
        {icon ? <img src={icon} alt="" draggable={false} /> : plugin.group === "ai" ? <Icons.Brain /> : <Icons.Puzzle />}
      </span>
      <span className="setup-wizard-plugin-text">
        <span className="setup-wizard-plugin-name">{name}</span>
        {row?.phase === "error" && row.detail ? <span className="setup-wizard-plugin-error">{row.detail}</span> : null}
        {row?.phase === "installing" ? <span className="setup-wizard-plugin-slow">Still downloading…</span> : null}
      </span>
      {desc ? <HelpTip label={`About ${name}`} text={desc} /> : null}
      <StatusIcon row={row} name={name} />
    </li>
  );
}

function PluginsStep({
  plugins,
  rows,
  installing,
  installError,
  items,
}: Pick<SetupWizardProps, "plugins" | "rows" | "installing" | "installError"> & {
  items: Record<string, DuckyOSStoreItemDto>;
}) {
  const done = plugins.filter((p) => rows[p.slug]?.phase === "installed").length;
  return (
    <>
      <h2 id="setup-wizard-title">Starter plugins</h2>
      <p className="setup-wizard-lead">
        Everything most people start with, installed in one go.{" "}
        <HelpTip
          label="About plugins"
          text="Ducky's features come as plugins from the UEFN Ducky Store. You can add or remove any of them later in Settings → Store."
        />
      </p>
      <div className="setup-wizard-progress">
        <progress
          className={`setup-wizard-progress-bar${installing ? " is-active" : ""}`}
          value={done}
          max={Math.max(plugins.length, 1)}
          aria-label="Plugins installed"
        />
        <span className="setup-wizard-progress-text">
          {done} of {plugins.length}
        </span>
      </div>
      {installError ? <p className="setup-wizard-error">{installError}</p> : null}
      {GROUPS.map((g) => {
        const list = plugins.filter((p) => p.group === g.id);
        if (!list.length) return null;
        return (
          <section key={g.id} className="setup-wizard-group" aria-label={g.title}>
            <h3>
              {g.title} <HelpTip label={`About ${g.title}`} text={g.tip} />
            </h3>
            <ul className="setup-wizard-plugin-list">
              {list.map((p) => (
                <PluginTile key={p.slug} plugin={p} row={rows[p.slug]} item={items[p.slug]} />
              ))}
            </ul>
          </section>
        );
      })}
    </>
  );
}

type KeyState = { text: string; ok: boolean; pending?: boolean };

export type AgentConnection = "logged_in" | "ready" | "logged_out" | "missing" | "checking";

/** Where a coding agent stands on this PC, as Settings → LLMs → Coding agent shows it. */
export function agentConnection(agent: CodingAgentDto): AgentConnection {
  if (!agent.available) return /checking/i.test(agent.status || "") ? "checking" : "missing";
  if (agent.logged_in === true) return "logged_in";
  if (agent.logged_in === false) return "logged_out";
  return "ready"; // installed, and it has no account to sign in to
}

const AGENT_STATE_LABEL: Record<AgentConnection, string> = {
  logged_in: "Logged in",
  ready: "Ready",
  logged_out: "Not logged in",
  missing: "Not installed",
  checking: "Checking…",
};

function isConnectedAgent(agent: CodingAgentDto): boolean {
  const state = agentConnection(agent);
  return state === "logged_in" || state === "ready";
}

/** Coding agents by the gateway plugin that registered them (Ducky's own agent left out). */
export function agentsByPlugin(agents: CodingAgentDto[]): Map<string, CodingAgentDto[]> {
  const out = new Map<string, CodingAgentDto[]>();
  for (const a of agents) {
    const pid = (a.plugin_id || "").trim().toLowerCase();
    if (!pid || a.id === "ducky") continue;
    out.set(pid, [...(out.get(pid) || []), a]);
  }
  return out;
}

/** The program each gateway sets Ducky's MCP tools up in, when its plugin doesn't say. */
const IDE_BY_PLUGIN: Record<string, { kind: string; label: string }> = {
  anthropic: { kind: "claude", label: "Claude Code" },
  openai: { kind: "codex", label: "Codex" },
  cursor: { kind: "cursor", label: "Cursor" },
  google: { kind: "antigravity", label: "Antigravity" },
};

/** The Account plugin's provider: Ducky AI, connected by signing this PC in, not by a key. */
const DUCKY_AI_PLUGIN = "account";

/** A gateway plugin as the anonymous counts name its AI. */
const AI_NAME: Record<string, string> = { anthropic: "claude", openai: "codex", google: "gemini", cursor: "cursor", ollama: "ollama" };

export type AiResult = { provider: string; state: "plan" | "key" | "account" | "not_logged_in" | "not_installed" | "none" };

/**
 * How setup left each AI, for the anonymous counts sent at Finish: connected with a plan you
 * pay for, an API key or the Ducky account, or why not (signed out, not installed, nothing).
 */
export function aiResults(
  providers: PluginLlmProvider[],
  keySaved: Record<string, boolean>,
  agentsFor: (p: PluginLlmProvider) => CodingAgentDto[],
  duckyAi: boolean,
): AiResult[] {
  const out: AiResult[] = providers.map((p) => {
    const agents = agentsFor(p);
    const states = agents.map(agentConnection);
    const provider = AI_NAME[p.plugin_id.toLowerCase()] || "other";
    if (states.some((s) => s === "logged_in" || s === "ready")) return { provider, state: "plan" };
    if (keySaved[p.secret_key || p.id]) return { provider, state: "key" };
    if (states.includes("logged_out")) return { provider, state: "not_logged_in" };
    if (states.length && states.every((s) => s === "missing")) return { provider, state: "not_installed" };
    return { provider, state: "none" };
  });
  out.push({ provider: "ducky_ai", state: duckyAi ? "account" : "none" });
  return out;
}

/** Keys, coding agents, MCP hookups, the Ducky account, and the chat providers, kept current. */
function useConnections() {
  const contrib = usePluginContributions();
  const [keySaved, setKeySaved] = useState<Record<string, boolean>>({});
  const [agents, setAgents] = useState<CodingAgentDto[]>([]);
  const [ides, setIdes] = useState<Record<string, boolean>>({});
  const [account, setAccount] = useState<DuckyOSAccountStatus>({});

  const refresh = useCallback(() => {
    const api = getApi();
    if (!api) return;
    void Promise.resolve(api.get_key_status())
      .then((s) => setKeySaved(s || {}))
      .catch(() => undefined);
    void Promise.resolve(api.list_coding_agents())
      .then((r) => setAgents(r?.agents || []))
      .catch(() => undefined);
    if (api.get_ide_statuses) {
      void Promise.resolve(api.get_ide_statuses())
        .then((rows) => setIdes(Object.fromEntries(Object.entries(rows || {}).map(([k, v]) => [k, !!v?.ok]))))
        .catch(() => undefined);
    }
    if (api.duckyos_get_status) {
      void Promise.resolve(api.duckyos_get_status())
        .then((s) => setAccount(s || {}))
        .catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    refresh();
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type === "coding_agents_updated" || event.type === "uefn_plugins_changed") refresh();
    });
  }, [refresh]);

  const providers = useMemo(
    () =>
      contrib.llm_providers
        .filter((p) => p.chat !== false && p.plugin_id.toLowerCase() !== DUCKY_AI_PLUGIN)
        .slice()
        .sort((a, b) => (a.order ?? 100) - (b.order ?? 100)),
    [contrib.llm_providers],
  );
  const byPlugin = useMemo(() => agentsByPlugin(agents), [agents]);
  const agentsFor = (p: PluginLlmProvider) => byPlugin.get(p.plugin_id.toLowerCase()) || [];
  const ideFor = (p: PluginLlmProvider) => {
    const pid = p.plugin_id.toLowerCase();
    const hook = contrib.ide_hookups?.find((h) => (h.plugin_id || "").toLowerCase() === pid);
    return hook ? { kind: hook.kind, label: hook.label || IDE_BY_PLUGIN[pid]?.label || hook.kind } : IDE_BY_PLUGIN[pid];
  };
  const isConnected = (p: PluginLlmProvider) => !!keySaved[p.secret_key || p.id] || agentsFor(p).some(isConnectedAgent);
  // Agents from a gateway that adds no provider row of its own still get a row.
  const shown = new Set([...providers.map((p) => p.plugin_id.toLowerCase()), DUCKY_AI_PLUGIN]);
  const otherAgents = [...byPlugin.entries()].filter(([pid]) => !shown.has(pid)).flatMap(([, list]) => list);
  const connectedNames = [
    ...(account.logged_in ? ["Ducky AI"] : []),
    ...providers.filter(isConnected).map((p) => p.label),
  ];
  return {
    providers, keySaved, agentsFor, ideFor, ides, account, setAccount, isConnected, otherAgents, connectedNames, refresh,
    ai: () => aiResults(providers, keySaved, agentsFor, !!account.logged_in),
  };
}

/**
 * A plan you already pay for (Claude Code, Codex, Gemini CLI, Cursor). Connect logs it in when it
 * needs to, then sets up Ducky's MCP tools in that program, so it can work on your island too.
 */
function AgentRow({
  agent,
  ide,
  ideReady,
  onChanged,
}: {
  agent: CodingAgentDto;
  ide?: { kind: string; label: string };
  ideReady: boolean;
  onChanged: () => void;
}) {
  const [loginOpen, setLoginOpen] = useState(false);
  const [busy, setBusy] = useState<"" | "check" | "mcp">("");
  const [note, setNote] = useState("");
  const state = agentConnection(agent);
  const ok = isConnectedAgent(agent);

  const setUpMcp = async () => {
    const api = getApi();
    if (!ide || !api?.apply_ide) return;
    setBusy("mcp");
    try {
      await api.apply_ide(ide.kind);
    } catch (err) {
      setNote(err instanceof Error ? err.message : `Couldn't set Ducky up in ${ide.label}`);
    } finally {
      setBusy("");
      onChanged();
    }
  };

  const check = async () => {
    const api = getApi();
    if (!api?.detect_coding_agent_cli) return;
    setBusy("check");
    setNote("");
    try {
      const res = await api.detect_coding_agent_cli(agent.id);
      if (res && res.ok === false) setNote(String(res.error || res.status || "Check failed"));
      onChanged();
      void refreshModelsCatalog();
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Check failed");
    } finally {
      setBusy("");
    }
  };

  const connected = ok && (!ide || ideReady);
  let help = "";
  if (state === "missing") help = (agent.install_help || "").trim();
  else if (state === "logged_out" && !agent.can_login) help = `Sign in with ${agent.label} itself, then press Check.`;
  return (
    <li className={`setup-wizard-agent is-${state}`}>
      <span className="setup-wizard-agent-name">{agent.label}</span>
      {help ? <HelpTip label={`How to set up ${agent.label}`} text={help} /> : null}
      <span className={`setup-wizard-pill${ok ? " is-ok" : ""}`} aria-label={`${agent.label}: ${AGENT_STATE_LABEL[state]}`}>
        {ok ? <Icons.Check /> : null} {AGENT_STATE_LABEL[state]}
      </span>
      {connected ? null : state === "logged_out" && agent.can_login ? (
        <button type="button" className="setup-wizard-btn is-primary" onClick={() => setLoginOpen(true)}>
          Connect
        </button>
      ) : ok ? (
        <button type="button" className="setup-wizard-btn is-primary" disabled={!!busy} onClick={() => void setUpMcp()}>
          {busy === "mcp" ? "Connecting…" : "Connect"}
        </button>
      ) : state === "checking" ? null : (
        <button
          type="button"
          className="setup-wizard-btn is-ghost"
          disabled={!!busy}
          title="Look again: after you installed it or signed in somewhere else"
          onClick={() => void check()}
        >
          {busy === "check" ? "Checking…" : "Check"}
        </button>
      )}
      {ide && ideReady ? (
        <span className="setup-wizard-agent-note">
          <Icons.Check /> Ducky's tools are set up in {ide.label}
        </span>
      ) : null}
      {note ? <span className="setup-wizard-agent-note is-error">{note}</span> : null}
      {loginOpen ? (
        <CodingAgentLoginModal
          agentId={agent.id}
          title={`Log in to ${agent.label}`}
          zIndex={2147483300}
          onClose={() => {
            void getApi()?.coding_agent_login_cancel?.(agent.id);
            setLoginOpen(false);
          }}
          onLoggedIn={() => {
            setLoginOpen(false);
            void refreshModelsCatalog();
            void setUpMcp();
          }}
        />
      ) : null}
    </li>
  );
}

function ProviderRow({
  provider,
  agents,
  ide,
  ideReady,
  saved,
  connected,
  open,
  onToggle,
  onSaved,
}: {
  provider: PluginLlmProvider;
  agents: CodingAgentDto[];
  ide?: { kind: string; label: string };
  ideReady: boolean;
  saved: boolean;
  connected: boolean;
  open: boolean;
  onToggle: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [state, setState] = useState<KeyState | null>(null);
  const secretKey = provider.secret_key || provider.id;

  const testAndSave = async () => {
    const value = draft.trim();
    if (!value) return;
    setState({ text: "Testing the key…", ok: true, pending: true });
    try {
      const { runBridgeJob } = await import("../hooks/bridgeJobAsync");
      const res = await runBridgeJob<{ ok: boolean; detail?: string; error?: string }>(
        "test_key",
        [secretKey, value],
        300_000,
      );
      if (!res.ok) {
        setState({ text: res.detail || res.error || "That key didn't work.", ok: false });
        return;
      }
      await getApi()?.save_agent_settings({ keys: { [secretKey]: value } });
      setDraft("");
      setState({ text: "Key saved.", ok: true });
      onSaved();
      void refreshModelsCatalog();
    } catch (err) {
      setState({ text: err instanceof Error ? err.message : "Test failed", ok: false });
    }
  };

  return (
    <li className={`setup-wizard-provider${connected ? " is-connected" : ""}${open ? " is-open" : ""}`}>
      <button type="button" className="setup-wizard-provider-head" aria-expanded={open} onClick={onToggle}>
        <span className="setup-wizard-provider-logo" aria-hidden>
          {provider.icon_data_url ? <img src={provider.icon_data_url} alt="" draggable={false} /> : <Icons.Brain />}
        </span>
        <span className="setup-wizard-provider-name">{provider.label}</span>
        <span
          className={`setup-wizard-pill${connected ? " is-ok" : ""}`}
          aria-label={`${provider.label}: ${connected ? "Connected" : "Not connected"}`}
        >
          {connected ? (
            <>
              <Icons.Check /> Connected
            </>
          ) : (
            "Not connected"
          )}
        </span>
        <span className="setup-wizard-provider-caret" aria-hidden>
          <Icons.ChevronDown />
        </span>
      </button>
      {open ? (
        <div className="setup-wizard-provider-body">
          {agents.length ? (
            <section className="setup-wizard-way" aria-label={`${provider.label}: your plan`}>
              <h4>
                <Icons.Terminal /> Your plan{" "}
                <HelpTip
                  label={`About ${provider.label} plans`}
                  text={`Use the ${provider.label} plan you already pay for: no key, no extra bill. Connect also sets up Ducky's tools (MCP) in ${ide?.label || "that app"}, so it can work on your island too.`}
                />
              </h4>
              <ul className="setup-wizard-agents" aria-label={`${provider.label} coding agents`}>
                {agents.map((a) => (
                  <AgentRow key={a.id} agent={a} ide={ide} ideReady={ideReady} onChanged={onSaved} />
                ))}
              </ul>
            </section>
          ) : null}
          {provider.kind === "secret" ? (
            <section className="setup-wizard-way" aria-label={`${provider.label}: API key`}>
              <h4>
                <Icons.Key /> {agents.length ? "Or an API key" : "API key"}
                {saved ? (
                  <span className="setup-wizard-pill is-ok" aria-label={`${provider.label} API key: Key saved`}>
                    <Icons.Check /> Saved
                  </span>
                ) : null}
              </h4>
              <div className="setup-wizard-key-row">
                <input
                  type="password"
                  className="setup-wizard-input"
                  placeholder={saved ? "Saved. Paste a new one to replace it" : `Paste your ${provider.label} API key`}
                  aria-label={`${provider.label} API key`}
                  autoComplete="off"
                  spellCheck={false}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void testAndSave();
                  }}
                />
                <button
                  type="button"
                  className="setup-wizard-btn"
                  disabled={!draft.trim() || !!state?.pending}
                  onClick={() => void testAndSave()}
                >
                  {state?.pending ? "Testing…" : "Test & save"}
                </button>
              </div>
              {state ? <p className={`setup-wizard-key-status${state.ok ? "" : " is-error"}`}>{state.text}</p> : null}
            </section>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

/**
 * The Ducky site's Connect page for this PC's sign-in code: it signs in (or makes an account) and
 * comes back with the code, then the PC is named and connected there.
 */
export function pairPath(code: string): string {
  return `/connect?code=${encodeURIComponent(code.toUpperCase().replace(/\s+/g, ""))}`;
}

/** Ducky AI: sign this PC in to a Ducky account (or make one), and Ducky AI is ready to chat. */
function DuckyAiCard({
  account,
  onAccount,
  onChanged,
  recommended = false,
}: {
  account: DuckyOSAccountStatus;
  onAccount: (next: DuckyOSAccountStatus) => void;
  onChanged: () => void;
  /** No coding CLI on this PC and no key: Ducky AI is the one way in that needs no install. */
  recommended?: boolean;
}) {
  const [pairing, setPairing] = useState(false);
  const [error, setError] = useState("");
  const opened = useRef("");

  const openPage = (code: string) => {
    opened.current = code;
    void getApi()?.duckyos_open_site?.(pairPath(code));
  };

  const signIn = async () => {
    const api = getApi();
    if (!api?.duckyos_login) return;
    setError("");
    setPairing(true);
    // The code shows up while the sign-in waits for the website; open its page once it does.
    const timer = window.setInterval(() => {
      void Promise.resolve(api.duckyos_get_status())
        .then((s) => {
          onAccount(s || {});
          if (s?.user_code && s.user_code !== opened.current) openPage(s.user_code);
        })
        .catch(() => undefined);
    }, 1200);
    try {
      const res = await api.duckyos_login();
      onAccount(res || {});
      if (res && !res.logged_in && res.code !== "cancelled") setError(res.error || "Sign-in didn't finish. Try again.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      window.clearInterval(timer);
      opened.current = "";
      setPairing(false);
      onChanged();
      void refreshModelsCatalog();
    }
  };

  const signedIn = !!account.logged_in;
  const code = pairing ? account.user_code || "" : "";
  return (
    <section className={`setup-wizard-ducky-ai${signedIn ? " is-connected" : ""}`} aria-label="Ducky AI">
      <span className="setup-wizard-chip is-amber" aria-hidden>
        <Icons.Duck />
      </span>
      <div className="setup-wizard-ducky-ai-text">
        <strong>
          Ducky AI{" "}
          <HelpTip
            label="About Ducky AI"
            text="Sign in to your Ducky account, or make one in the browser: all you do there is name this PC. Ducky AI is then ready in your chats with no key. The same account opens the phone app, and every PC you connect can be run from one UEFN Ducky."
          />
          {recommended && !signedIn ? <span className="setup-wizard-pill is-amber">Recommended: nothing to install</span> : null}
        </strong>
        <span className="setup-wizard-muted">
          {signedIn
            ? `Signed in${account.email ? ` as ${account.email}` : ""}. This PC is connected.`
            : code
              ? `Name this PC in your browser (code ${code}).`
              : "Sign in or make an account. This PC connects right away."}
        </span>
        <span className="setup-wizard-ducky-ai-extra">Also: the phone app, and your other PCs run from this one.</span>
        {error ? <span className="setup-wizard-agent-note is-error">{error}</span> : null}
      </div>
      {signedIn ? (
        <span className="setup-wizard-pill is-ok" aria-label="Ducky AI: Connected">
          <Icons.Check /> Connected
        </span>
      ) : pairing ? (
        <span className="setup-wizard-foot-right">
          {code ? (
            <button type="button" className="setup-wizard-link" onClick={() => openPage(code)}>
              Open the page again
            </button>
          ) : null}
          <button type="button" className="setup-wizard-btn is-ghost" onClick={() => void getApi()?.duckyos_cancel_login?.()}>
            Cancel
          </button>
        </span>
      ) : (
        <button type="button" className="setup-wizard-btn is-primary" onClick={() => void signIn()}>
          Sign in or sign up
        </button>
      )}
    </section>
  );
}

function AiStep({ onInstallStep }: { onInstallStep: () => void }) {
  const { providers, keySaved, agentsFor, ideFor, ides, account, setAccount, isConnected, otherAgents, refresh } =
    useConnections();
  const [open, setOpen] = useState<string | null>(null);
  // Start with the first provider that still needs connecting open.
  const firstOpen = useRef(false);
  useEffect(() => {
    if (firstOpen.current || !providers.length) return;
    firstOpen.current = true;
    setOpen((providers.find((p) => !isConnected(p)) || providers[0])!.id);
  });
  // Day-0 uninstalls said "setup was hard" (Oct 27 2026): most new PCs had no coding CLI at all.
  const cliAgents = providers.flatMap(agentsFor);
  const nothingInstalled =
    cliAgents.length > 0 &&
    cliAgents.every((a) => agentConnection(a) === "missing") &&
    !Object.values(keySaved).some(Boolean);

  return (
    <>
      <h2 id="setup-wizard-title">Connect your AI</h2>
      <p className="setup-wizard-lead">
        Sign in to Ducky AI, or connect a plan you already pay for.{" "}
        <HelpTip
          label="Ways to connect"
          text="Ducky AI: sign this PC in to your Ducky account. Your plan: log in to Claude Code, Codex, Gemini CLI or Cursor and use the plan you already pay for; Connect also sets up Ducky's tools in that app. API key: paste a key from the provider's website; what you use is billed by that provider."
        />
      </p>
      <DuckyAiCard account={account} onAccount={setAccount} onChanged={refresh} recommended={nothingInstalled} />
      {providers.length || otherAgents.length ? (
        <ul className="setup-wizard-providers">
          {providers.map((p) => {
            const ide = ideFor(p);
            return (
              <ProviderRow
                key={p.id}
                provider={p}
                agents={agentsFor(p)}
                ide={ide}
                ideReady={!!ide && !!ides[ide.kind]}
                saved={!!keySaved[p.secret_key || p.id]}
                connected={isConnected(p)}
                open={open === p.id}
                onToggle={() => setOpen((cur) => (cur === p.id ? null : p.id))}
                onSaved={refresh}
              />
            );
          })}
          {otherAgents.length ? (
            <li className="setup-wizard-provider is-open">
              <div className="setup-wizard-provider-body">
                <ul className="setup-wizard-agents" aria-label="Other coding agents">
                  {otherAgents.map((a) => (
                    <AgentRow key={a.id} agent={a} ideReady={false} onChanged={refresh} />
                  ))}
                </ul>
              </div>
            </li>
          ) : null}
        </ul>
      ) : (
        <div className="setup-wizard-empty">
          <p>No AI plugin is installed yet.</p>
          <button type="button" className="setup-wizard-link" onClick={onInstallStep}>
            Back to plugins
          </button>
        </div>
      )}
    </>
  );
}

/** Each job that has its own model, what to pick for it, and where it is saved. */
type ModelJob = { id: "chat" | "live" | "spoken" | "memory"; title: string; suggest: string; empty: string };
export const MODEL_JOBS: ModelJob[] = [
  {
    id: "chat",
    title: "Chats and duckies",
    suggest: "Your strongest model: it writes Verse, builds levels and uses tools. Try Claude Sonnet or Opus, GPT or Gemini Pro.",
    empty: "Pick a model",
  },
  {
    id: "live",
    title: "Live voice",
    suggest: "A fast, small model that answers you out loud right away while the ducky keeps working. Try Claude Haiku, GPT mini or Gemini Flash.",
    empty: "The chat's model",
  },
  {
    id: "spoken",
    title: "Spoken summaries",
    suggest: "Your cheapest model: it only shortens replies before they are read aloud.",
    empty: "Chats model",
  },
  {
    id: "memory",
    title: "Memory summaries",
    suggest: "A cheap model that shrinks older messages in long chats. Empty uses the spoken summaries model, then the chats model.",
    empty: "Spoken summaries model",
  },
];

export const MAKER_JOBS: { kind: MediaKind; title: string; suggest: string }[] = [
  { kind: "image", title: "Pictures", suggest: "Makes the pictures duckies and workflows ask for: Ducky AI, or your own OpenAI or Google key." },
  { kind: "mesh", title: "3D models", suggest: "Makes 3D models from text or a picture: Meshy, Tripo or 3D AI Studio." },
];

function saveJobModel(id: ModelJob["id"], next: string) {
  const api = getApi();
  if (id === "chat") void api?.save_agent_settings({ default_model: next });
  else if (id === "live") void saveVoiceSettings({ liveModel: next });
  else if (id === "spoken") void saveVoiceSettings({ summaryModel: next });
  else void api?.set_memory_settings?.({ memory_summary_model: next });
}

function DefaultsStep({
  plugins,
  rows,
  installing,
  onInstall,
  onStep,
  onAi,
}: Pick<SetupWizardProps, "plugins" | "rows" | "installing" | "onInstall" | "onStep"> & { onAi: (results: AiResult[]) => void }) {
  const { providers, ideFor, ides, connectedNames, ai } = useConnections();
  // Kept current for Finish, which sends how each AI ended up.
  useEffect(() => {
    onAi(ai());
  });
  const [models, setModels] = useState<Record<ModelJob["id"], string>>({ chat: "", live: "", spoken: "", memory: "" });
  const [loaded, setLoaded] = useState(false);
  const model = models.chat;

  useEffect(() => {
    void refreshModelsCatalog();
    const api = getApi();
    if (!api?.get_settings) return;
    const put = (patch: Partial<Record<ModelJob["id"], string>>) => setModels((cur) => ({ ...cur, ...patch }));
    void Promise.resolve(api.get_settings())
      .then((s) => put({ chat: ((s as { default_model?: string })?.default_model || "").trim() }))
      .catch(() => undefined)
      .finally(() => setLoaded(true));
    void loadVoiceSettings()
      .then((v) => put({ live: v.liveModel, spoken: v.summaryModel }))
      .catch(() => undefined);
    void Promise.resolve(api.get_memory_settings?.())
      .then((m) => put({ memory: String(m?.memory_summary_model || "") }))
      .catch(() => undefined);
  }, []);

  const save = (id: ModelJob["id"], next: string) => {
    setModels((cur) => ({ ...cur, [id]: next }));
    saveJobModel(id, next);
  };

  const installed = plugins.filter((p) => rows[p.slug]?.phase === "installed").length;
  const failed = plugins.filter((p) => rows[p.slug]?.phase === "error");
  const toolsIn = [
    ...new Set(
      providers
        .map(ideFor)
        .filter((ide): ide is { kind: string; label: string } => !!ide && !!ides[ide.kind])
        .map((ide) => ide.label),
    ),
  ];

  return (
    <>
      <h2 id="setup-wizard-title">Your defaults</h2>
      <p className="setup-wizard-lead">New chats start with these. Change them any time in Settings.</p>

      <section className="setup-wizard-card" aria-label="Models">
        <h3>
          Model for each job{" "}
          <HelpTip
            label="About models for each job"
            text="Any connected model works for any job. Pick a coding agent (Claude Code, Codex, Cursor) for chats to run them on your plan. Change these any time in Settings → LLMs, Audio and Memory."
          />
        </h3>
        {connectedNames.length ? (
          <ul className="setup-wizard-jobs">
            {MODEL_JOBS.map((job) => (
              <li key={job.id} className="setup-wizard-job" aria-label={job.title}>
                <span className="setup-wizard-job-text">
                  <strong>{job.title}</strong>
                  <span className="setup-wizard-muted">{job.suggest}</span>
                </span>
                <DuckyModelPicker
                  model={models[job.id]}
                  onChange={(next) => save(job.id, next)}
                  allowClear={job.id !== "chat"}
                  requireTools={job.id === "chat"}
                  placeholder={loaded ? job.empty : "Loading…"}
                  label=""
                  hint=""
                  menuPlacement="bottom"
                  labeled
                  menuZIndex={2147483300}
                />
              </li>
            ))}
          </ul>
        ) : (
          <p className="setup-wizard-muted">
            Connect an AI first.{" "}
            <button type="button" className="setup-wizard-link" onClick={() => onStep("ai")}>
              Connect AI
            </button>
          </p>
        )}
      </section>

      <section className="setup-wizard-card" aria-label="Pictures and 3D models">
        <h3>
          Pictures and 3D models{" "}
          <HelpTip
            label="About pictures and 3D models"
            text="Each costs credits or your own API key a run, and a ducky asks you before it spends. Change these any time in Settings → LLMs."
          />
        </h3>
        <ul className="setup-wizard-jobs">
          {MAKER_JOBS.map((job) => (
            <li key={job.kind} className="setup-wizard-job" aria-label={job.title}>
              <span className="setup-wizard-job-text">
                <strong>{job.title}</strong>
                <span className="setup-wizard-muted">{job.suggest}</span>
              </span>
              <MediaMakerPicker kind={job.kind} label={job.title} />
            </li>
          ))}
        </ul>
      </section>

      <section className="setup-wizard-card" aria-label="Overview">
        <h3>Overview</h3>
        <ul className="setup-wizard-overview">
          <li className={failed.length ? "is-warn" : installed === plugins.length ? "is-ok" : "is-busy"}>
            {failed.length ? <Icons.AlertTriangle /> : installed === plugins.length ? <Icons.Check /> : <Icons.Spinner />}
            <span>
              Plugins: {installed} of {plugins.length}
              {installing ? " (installing)" : ""}
            </span>
            {failed.length && !installing ? (
              <button type="button" className="setup-wizard-link" onClick={onInstall}>
                Try again
              </button>
            ) : null}
          </li>
          <li className={connectedNames.length ? "is-ok" : "is-warn"}>
            {connectedNames.length ? <Icons.Check /> : <Icons.AlertTriangle />}
            <span>AI: {connectedNames.length ? connectedNames.join(", ") : "not connected"}</span>
          </li>
          <li className={model ? "is-ok" : "is-warn"}>
            {model ? <Icons.Check /> : <Icons.AlertTriangle />}
            <span>Default AI: {model ? model.slice(model.indexOf(":") + 1) : "not picked"}</span>
          </li>
          <li className={toolsIn.length ? "is-ok" : ""}>
            {toolsIn.length ? <Icons.Check /> : <Icons.Terminal />}
            <span>Ducky's tools in: {toolsIn.length ? toolsIn.join(", ") : "no other app yet"}</span>
          </li>
        </ul>
      </section>
    </>
  );
}

export function SetupWizard(props: SetupWizardProps) {
  const { step, onStep, plugins, rows, installing, onInstall, onFinish, onClose } = props;
  const aiAtFinish = useRef<AiResult[]>([]);
  // Loaded from Welcome on: a fresh catalog (with its icons) can take a while to arrive.
  const items = useStoreItems();
  const missing = plugins.filter((p) => rows[p.slug]?.phase !== "installed");
  const failed = plugins.some((p) => rows[p.slug]?.phase === "error");
  const done = plugins.length - missing.length;
  // One AI plugin is enough to go on: the rest keep installing behind the next steps.
  const aiReady = plugins.some((p) => p.group === "ai" && rows[p.slug]?.phase === "installed");
  const canNext = missing.length === 0 || (aiReady && (installing || failed));

  // A click that lands as the button under it changes (a double click, or Installing… turning into
  // Next) must not skip a step: after a change the footer's main button waits a moment.
  const primaryKey = `${step}:${canNext}:${installing}`;
  const shown = useRef(primaryKey);
  const armedAt = useRef(0);
  useEffect(() => {
    if (shown.current === primaryKey) return;
    shown.current = primaryKey;
    armedAt.current = Date.now() + 600;
  }, [primaryKey]);
  const settled = (fn: () => void) => () => {
    if (Date.now() >= armedAt.current) fn();
  };

  useEffect(() => {
    if (!onClose) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !installing) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [installing, onClose]);

  const back = (to: SetupStep, disabled = false) => (
    <button type="button" className="setup-wizard-btn is-ghost" disabled={disabled} onClick={() => onStep(to)}>
      <Icons.ChevronLeft /> Back
    </button>
  );

  let footer: ReactNode;
  if (step === "welcome") {
    footer = (
      <>
        <span />
        <button type="button" className="setup-wizard-btn is-primary" onClick={settled(() => onStep("plugins"))}>
          Get started <Icons.ChevronRight />
        </button>
      </>
    );
  } else if (step === "plugins") {
    let primary: ReactNode;
    if (canNext) {
      primary = (
        <button type="button" className="setup-wizard-btn is-primary" onClick={settled(() => onStep("ai"))}>
          Next <Icons.ChevronRight />
        </button>
      );
    } else if (installing) {
      primary = (
        <button type="button" className="setup-wizard-btn is-primary is-busy" disabled>
          <Icons.Spinner /> Installing {Math.min(done + 1, plugins.length)} of {plugins.length}…
        </button>
      );
    } else {
      primary = (
        <button type="button" className="setup-wizard-btn is-primary" onClick={settled(onInstall)}>
          <Icons.Download /> {failed ? "Try again" : `Install ${missing.length} plugin${missing.length === 1 ? "" : "s"}`}
        </button>
      );
    }
    footer = (
      <>
        {back("welcome", installing)}
        <span className="setup-wizard-foot-right">
          {installing && aiReady ? (
            <span className="setup-wizard-muted">
              <Icons.Spinner /> {missing.length} more installing
            </span>
          ) : null}
          {failed && aiReady && !installing ? (
            <button type="button" className="setup-wizard-btn is-ghost" onClick={onInstall}>
              Try again
            </button>
          ) : null}
          {primary}
        </span>
      </>
    );
  } else if (step === "ai") {
    footer = (
      <>
        {back("plugins")}
        <button type="button" className="setup-wizard-btn is-primary" onClick={settled(() => onStep("defaults"))}>
          Next <Icons.ChevronRight />
        </button>
      </>
    );
  } else {
    footer = (
      <>
        {back("ai")}
        <button
          type="button"
          className="setup-wizard-btn is-primary"
          onClick={settled(() => {
            for (const r of aiAtFinish.current) usageTrack("setup.ai", r);
            onFinish();
          })}
        >
          Finish <Icons.Check />
        </button>
      </>
    );
  }

  return createPortal(
    <div className="setup-wizard-backdrop no-drag">
      <section className="setup-wizard" role="dialog" aria-modal="true" aria-labelledby="setup-wizard-title">
        <header className="setup-wizard-top">
          <StepDots step={step} />
          {onClose ? (
            <button
              type="button"
              className="setup-wizard-close"
              aria-label="Close setup"
              disabled={installing}
              onClick={onClose}
            >
              <Icons.Close />
            </button>
          ) : null}
        </header>
        <div className={`setup-wizard-body is-${step}`}>
          {step === "welcome" ? <WelcomeStep /> : null}
          {step === "plugins" ? (
            <PluginsStep
              plugins={plugins}
              rows={rows}
              installing={installing}
              installError={props.installError}
              items={items}
            />
          ) : null}
          {step === "ai" ? <AiStep onInstallStep={() => onStep("plugins")} /> : null}
          {step === "defaults" ? (
            <DefaultsStep
              plugins={plugins}
              rows={rows}
              installing={installing}
              onInstall={onInstall}
              onStep={onStep}
              onAi={(r) => (aiAtFinish.current = r)}
            />
          ) : null}
        </div>
        <footer className="setup-wizard-foot">{footer}</footer>
      </section>
    </div>,
    document.body,
  );
}
