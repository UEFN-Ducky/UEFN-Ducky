import { runBridgeJob } from "../hooks/bridgeJobAsync";
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
import { fetchStoreCatalog, peekStoreCatalogCache } from "../hooks/storeCatalogCache";
import { refreshModelsCatalog } from "../hooks/modelsCatalogCache";
import { usageTrack } from "../hooks/usageTrack";
import { Icons } from "../icons/Icons";
import type { CodingAgentDto, DuckyOSAccountStatus, DuckyOSStoreItemDto, StarterPluginDto } from "../types/panel";
import { CodingAgentLoginModal } from "../views/settings/CodingAgentsSection";
import { BROWSER_WAIT, waitForInstall, waitForSignIn } from "../views/settings/gatewayLogin";
import { DuckyModelPicker } from "./ducky/DuckyModelPicker";
import { SetupModelTiles } from "./SetupModelTiles";
import { SetupStepDuck } from "./SetupStepDuck";
import { SetupWelcomeScene } from "./SetupWelcomeScene";
import { loadJobModels, MODEL_JOBS, NO_JOB_MODELS, saveJobModel, type JobModels, type ModelJob } from "./modelJobs";

export { MODEL_JOBS };
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
    // The catalog carries every icon: a slow link can time it out, so try again a few times.
    // fetchStoreCatalog shares the read with the Store badge (one 230 KB read at boot, not two).
    const load = (attempt: number): void => {
      void fetchStoreCatalog(api)
        .then((next) => {
          if (!alive) return;
          if (!next || next.ok === false || !next.items?.length) throw new Error("no catalog");
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
      <h2 id="setup-wizard-title">Welcome to UEFN Ducky</h2>
      <p className="setup-wizard-lead">Your AI crew for Fortnite islands.</p>
      <SetupWelcomeScene />
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

/** "Where did you hear about us?": ids match usage_events.SETUP_SOURCES. */
export const SOURCES: Array<{ id: string; label: string }> = [
  { id: "youtube", label: "YouTube" },
  { id: "tiktok", label: "TikTok" },
  { id: "discord", label: "Discord" },
  { id: "x", label: "X" },
  { id: "reddit", label: "Reddit" },
  { id: "google", label: "Google search" },
  { id: "epic_forums", label: "Epic forums" },
  { id: "friend", label: "A friend" },
  { id: "other", label: "Somewhere else" },
];

function SourcePicker({ picked, onPick }: { picked: string[]; onPick: (next: string[]) => void }) {
  return (
    <section className="setup-wizard-card" aria-label="Where did you hear about us?">
      <h3>Where did you hear about us?</h3>
      <p className="setup-wizard-muted">Tick all that apply, if you like.</p>
      <ul className="setup-source-grid">
        {SOURCES.map((s) => {
          const on = picked.includes(s.id);
          return (
            <li key={s.id}>
              <button
                type="button"
                role="checkbox"
                aria-checked={on}
                className={`setup-source${on ? " is-on" : ""}`}
                onClick={() => onPick(on ? picked.filter((p) => p !== s.id) : [...picked, s.id])}
              >
                <span className="setup-source-box" aria-hidden>
                  {on ? <Icons.Check /> : null}
                </span>
                {s.label}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
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
  const [busy, setBusy] = useState<"" | "check" | "mcp" | "connect">("");
  const [note, setNote] = useState("");
  // What Connect is waiting on: an install, or the sign-in in the browser.
  const [progress, setProgress] = useState("");
  const alive = useRef(true);
  useEffect(() => () => void (alive.current = false), []);
  const state = agentConnection(agent);
  const ok = isConnectedAgent(agent);
  // Connect also installs a missing program when its plugin can sign in from here.
  const canConnect = !!(agent.one_click_login || agent.can_login);

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

  /**
   * One click from "Not installed" or "Not logged in" to connected: the plugin installs the
   * program when it's missing, signs in (hidden, in the browser, when it can; else its link-and-
   * code window), then Ducky's tools are set up in that program. Same calls as Settings → LLMs.
   */
  const connect = async () => {
    const api = getApi();
    if (!api) return;
    setNote("");
    if (state === "logged_out" && !agent.one_click_login) {
      setLoginOpen(true);
      return;
    }
    setBusy("connect");
    const watch = { alive: () => alive.current, onWaiting: setProgress };
    let signedIn = false;
    try {
      const listed = await api.list_gateway_accounts?.(agent.id).catch(() => undefined);
      const account = (listed?.ok && (listed.default || listed.accounts[0]?.id)) || "default";
      if (!agent.one_click_login) {
        // Missing, with a link-and-code sign-in: looking again starts the plugin's install.
        setProgress(`Installing ${agent.label}…`);
        const found = await api.detect_coding_agent_cli?.(agent.id).catch(() => undefined);
        const install = found?.available ? { ran: true, ok: true, error: "" } : await waitForInstall(agent.id, account, watch);
        if (!alive.current) return;
        if (!install.ok) {
          setNote(install.error || String(found?.error || "") || `Couldn't install ${agent.label}. Install it yourself, then press Connect.`);
          return;
        }
        await api.detect_coding_agent_cli?.(agent.id).catch(() => undefined);
        onChanged();
        setLoginOpen(true);
        return;
      }
      for (let attempt = 0; attempt < 2 && !signedIn; attempt++) {
        const started = await api.login_gateway_account?.(agent.id, account).catch(() => undefined);
        if (!alive.current) return;
        if (started?.ok && started.waiting) {
          const done = await waitForSignIn(agent.id, account, started.message || BROWSER_WAIT, watch);
          if (!alive.current) return;
          if (!done.loggedIn) {
            setNote(done.text);
            return;
          }
          signedIn = true;
        } else if (started?.ok) {
          setNote("Finish signing in in the terminal tab, then press Connect.");
          return;
        } else {
          // Not started: the program is missing and the plugin is installing it; then once more.
          const install = await waitForInstall(agent.id, account, watch);
          if (!alive.current) return;
          if (!install.ok || attempt) {
            setNote(install.error || (started && !started.ok ? started.error : "") || "Couldn't start the sign-in. Try again.");
            return;
          }
        }
      }
      // Its row reads Logged in from a fresh look at the program.
      await api.detect_coding_agent_cli?.(agent.id).catch(() => undefined);
      void refreshModelsCatalog();
    } finally {
      if (alive.current) {
        setBusy("");
        setProgress("");
        if (!signedIn) onChanged();
      }
    }
    if (signedIn && alive.current) await setUpMcp();
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
      {connected ? null : (state === "logged_out" || state === "missing") && canConnect ? (
        <button type="button" className="setup-wizard-btn is-primary" disabled={!!busy} onClick={() => void connect()}>
          {busy === "connect" ? "Connecting…" : "Connect"}
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
      {progress ? (
        <span className="setup-wizard-agent-note" role="status">
          {progress}
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

/**
 * A Connect AI row's body. It stays mounted so its height slides open and shut (0fr → 1fr); closed,
 * it is inert and hidden from screen readers.
 */
function RowBody({ open, children }: { open: boolean; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.inert = !open;
  }, [open]);
  return (
    <div ref={ref} className="setup-wizard-provider-clip" aria-hidden={!open}>
      <div className="setup-wizard-provider-inner">
        <div className="setup-wizard-provider-body">{children}</div>
      </div>
    </div>
  );
}

/** Opens a provider's key page in the person's browser (the window can't navigate away). */
function openKeyPage(url: string) {
  const api = getApi();
  if (api?.open_external_url) void api.open_external_url(url);
  else window.open(url, "_blank", "noopener");
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

  // The plugin says the key is the way that works for most people (Google: Gemini CLI refuses
  // personal accounts; 9 of 12 Setup checks on Nov 10 2026 found Gemini not signed in).
  const keyFirst = Boolean(provider.key_first) && provider.kind === "secret";
  const planWay = agents.length ? (
    <section className="setup-wizard-way" aria-label={`${provider.label}: your plan`}>
      <h4>
        <Icons.Terminal /> {keyFirst ? "Or your plan" : "Your plan"}{" "}
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
  ) : null;
  const keyWay = provider.kind === "secret" ? (
    <section className="setup-wizard-way" aria-label={`${provider.label}: API key`}>
      <h4>
        <Icons.Key /> {agents.length && !keyFirst ? "Or an API key" : "API key"}
        {saved ? (
          <span className="setup-wizard-pill is-ok" aria-label={`${provider.label} API key: Key saved`}>
            <Icons.Check /> Saved
          </span>
        ) : provider.key_url ? (
          <button type="button" className="link-btn" onClick={() => openKeyPage(provider.key_url || "")}>
            Get a key
          </button>
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
  ) : null;

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
      <RowBody open={open}>
          {keyFirst ? keyWay : planWay}
          {keyFirst ? planWay : keyWay}
      </RowBody>
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

/** The Ducky AI row's id in Connect AI's one-open-at-a-time accordion. */
const DUCKY_AI_ROW = "ducky-ai";

/**
 * Ducky AI, the first Connect AI row (same look as the providers): optional; signing this PC in to a
 * Ducky account (or making one) unlocks Ducky AI, team features and the phone app.
 */
function DuckyAiRow({
  account,
  onAccount,
  onChanged,
  open,
  onToggle,
  recommended = false,
}: {
  account: DuckyOSAccountStatus;
  onAccount: (next: DuckyOSAccountStatus) => void;
  onChanged: () => void;
  open: boolean;
  onToggle: () => void;
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
    <li className={`setup-wizard-provider${signedIn ? " is-connected" : ""}${open ? " is-open" : ""}`}>
      <button type="button" className="setup-wizard-provider-head" aria-expanded={open} onClick={onToggle}>
        <span className="setup-wizard-provider-logo" aria-hidden>
          <img src="./OnlineMCPIcon.png" alt="" draggable={false} />
        </span>
        <span className="setup-wizard-provider-name">
          Ducky AI <span className="setup-wizard-provider-tag">Optional</span>
        </span>
        {recommended && !signedIn ? <span className="setup-wizard-pill">Recommended: nothing to install</span> : null}
        <span
          className={`setup-wizard-pill${signedIn ? " is-ok" : ""}`}
          aria-label={`Ducky AI: ${signedIn ? "Connected" : "Not connected"}`}
        >
          {signedIn ? (
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
      <RowBody open={open}>
        <section className="setup-wizard-way" aria-label="Ducky AI: your Ducky account">
          <p className="setup-wizard-muted">
            Optional. Skip it if you use your own plan or API key below. A Ducky account on this PC unlocks:
          </p>
          <ul className="setup-wizard-unlocks">
            <li>
              <Icons.Duck /> Ducky AI in your chats, with no key and nothing to install
            </li>
            <li>
              <Icons.GitBranch /> Team features: source control, shared plugins and going live together
            </li>
            <li>
              <Icons.Check /> The phone app, and your other PCs run from this one
            </li>
          </ul>
          <p className="setup-wizard-muted">
            {signedIn
              ? `Signed in${account.email ? ` as ${account.email}` : ""}. This PC is connected.`
              : code
                ? `Name this PC in your browser (code ${code}).`
                : "Sign in or make an account in your browser. This PC connects right away."}
          </p>
          {error ? <span className="setup-wizard-agent-note is-error">{error}</span> : null}
          {signedIn ? null : pairing ? (
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
            <button type="button" className="setup-wizard-btn is-primary setup-wizard-unlocks-cta" onClick={() => void signIn()}>
              Sign in or sign up
            </button>
          )}
        </section>
      </RowBody>
    </li>
  );
}

function AiStep({ onInstallStep }: { onInstallStep: () => void }) {
  const { providers, keySaved, agentsFor, ideFor, ides, account, setAccount, isConnected, otherAgents, refresh } =
    useConnections();
  const [open, setOpen] = useState<string | null>(null);
  // Day-0 uninstalls said "setup was hard" (Oct 27 2026): most new PCs had no coding CLI at all.
  const cliAgents = providers.flatMap(agentsFor);
  const nothingInstalled =
    cliAgents.length > 0 &&
    cliAgents.every((a) => agentConnection(a) === "missing") &&
    !Object.values(keySaved).some(Boolean);
  // Start with Ducky AI open while there is no provider (or nothing installed), else the first
  // provider that still needs connecting.
  const firstOpen = useRef(false);
  useEffect(() => {
    if (firstOpen.current) return;
    if (!providers.length || (nothingInstalled && !account.logged_in)) {
      setOpen(DUCKY_AI_ROW);
      return;
    }
    firstOpen.current = true;
    setOpen((providers.find((p) => !isConnected(p)) || providers[0])!.id);
  });
  const toggle = (id: string) => setOpen((cur) => (cur === id ? null : id));

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
      <ul className="setup-wizard-providers">
        <DuckyAiRow
          account={account}
          onAccount={setAccount}
          onChanged={refresh}
          open={open === DUCKY_AI_ROW}
          onToggle={() => toggle(DUCKY_AI_ROW)}
          recommended={nothingInstalled}
        />
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
                onToggle={() => toggle(p.id)}
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
      {providers.length || otherAgents.length ? null : (
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

function DefaultsStep({
  plugins,
  rows,
  installing,
  onInstall,
  onStep,
  onAi,
  sources,
  onSources,
}: Pick<SetupWizardProps, "plugins" | "rows" | "installing" | "onInstall" | "onStep"> & {
  onAi: (results: AiResult[]) => void;
  sources: string[];
  onSources: (next: string[]) => void;
}) {
  const { providers, ideFor, ides, connectedNames, ai } = useConnections();
  // Kept current for Finish, which sends how each AI ended up.
  useEffect(() => {
    onAi(ai());
  });
  const [models, setModels] = useState<JobModels>(NO_JOB_MODELS);
  const [loaded, setLoaded] = useState(false);
  const model = models.chat;

  useEffect(() => {
    void refreshModelsCatalog();
    void loadJobModels((patch) => setModels((cur) => ({ ...cur, ...patch }))).finally(() => setLoaded(true));
  }, []);

  const save = (id: ModelJob["id"], next: string) => {
    setModels((cur) => ({ ...cur, [id]: next }));
    void saveJobModel(id, next);
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
              <li key={job.id} className="setup-wizard-job is-tiles" aria-label={job.title}>
                <span className="setup-wizard-job-text">
                  <strong>{job.title}</strong>
                  <span className="setup-wizard-muted">{job.suggest}</span>
                </span>
                <SetupModelTiles
                  job={job.id}
                  value={models[job.id]}
                  onPick={(next) => save(job.id, next)}
                  sameLabel={job.id === "chat" ? undefined : job.empty}
                  other={
                    <DuckyModelPicker
                      model={models[job.id]}
                      onChange={(next) => save(job.id, next)}
                      allowClear={job.id !== "chat"}
                      requireTools={job.id === "chat"}
                      placeholder={loaded ? job.empty : "Loading…"}
                      label=""
                      hint=""
                      menuPlacement="top"
                      labeled
                      menuZIndex={2147483300}
                    />
                  }
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

      <SourcePicker picked={sources} onPick={onSources} />

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
  // No close and no skip: setup is gone through to the end, first run or opened again later.
  const { step, onStep, plugins, rows, installing, onInstall, onFinish } = props;
  const aiAtFinish = useRef<AiResult[]>([]);
  const [sources, setSources] = useState<string[]>([]);

  // Between steps the old step's pieces slide out one after another, then the new step's slide in
  // (from the right going on, from the left going back). No motion: the step just changes.
  const [onScreen, setOnScreen] = useState<SetupStep>(step);
  const [leaving, setLeaving] = useState(false);
  const goingBack = useRef(false);
  useEffect(() => {
    if (step === onScreen) return;
    goingBack.current = STEPS.findIndex((s) => s.id === step) < STEPS.findIndex((s) => s.id === onScreen);
    const moves = typeof window.matchMedia === "function" && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!moves) {
      setOnScreen(step);
      return;
    }
    setLeaving(true);
    const t = window.setTimeout(() => {
      setLeaving(false);
      setOnScreen(step);
    }, 220);
    return () => window.clearTimeout(t);
  }, [step, onScreen]);
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

  const backBtn = (to: SetupStep, disabled = false) => (
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
        {backBtn("welcome", installing)}
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
        {backBtn("plugins")}
        <button type="button" className="setup-wizard-btn is-primary" onClick={settled(() => onStep("defaults"))}>
          Next <Icons.ChevronRight />
        </button>
      </>
    );
  } else {
    footer = (
      <>
        {backBtn("ai")}
        <button
          type="button"
          className="setup-wizard-btn is-primary"
          onClick={settled(() => {
            for (const r of aiAtFinish.current) usageTrack("setup.ai", r);
            for (const source of sources) usageTrack("setup.source", { source });
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
        </header>
        {/* Its own key: sharing the body's key made React keep old duckies when going Back. */}
        {onScreen !== "welcome" ? <SetupStepDuck key={`duck-${onScreen}`} step={onScreen} /> : null}
        <div
          key={`body-${onScreen}`}
          className={`setup-wizard-body is-${onScreen}${leaving ? " is-leaving" : " is-entering"}${goingBack.current ? " is-back" : ""}`}
        >
          {onScreen === "welcome" ? <WelcomeStep /> : null}
          {onScreen === "plugins" ? (
            <PluginsStep
              plugins={plugins}
              rows={rows}
              installing={installing}
              installError={props.installError}
              items={items}
            />
          ) : null}
          {onScreen === "ai" ? <AiStep onInstallStep={() => onStep("plugins")} /> : null}
          {onScreen === "defaults" ? (
            <DefaultsStep
              plugins={plugins}
              rows={rows}
              installing={installing}
              onInstall={onInstall}
              onStep={onStep}
              onAi={(r) => (aiAtFinish.current = r)}
              sources={sources}
              onSources={setSources}
            />
          ) : null}
        </div>
        <footer className="setup-wizard-foot">{footer}</footer>
      </section>
    </div>,
    document.body,
  );
}
