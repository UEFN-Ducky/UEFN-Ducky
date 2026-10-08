/**
 * First-run setup: Welcome, the starter plugins (one Install, each row with its Store icon
 * and progress), then connecting an AI (an API key, or a coding agent you already pay for).
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { getApi } from "../hooks/usePanelApi";
import { usePluginContributions, type PluginLlmProvider } from "../hooks/usePluginContributions";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import { peekStoreCatalogCache, rememberStoreCatalog } from "../hooks/storeCatalogCache";
import { Icons } from "../icons/Icons";
import type { CodingAgentDto, DuckyOSStoreItemDto, StarterPluginDto } from "../types/panel";

export type SetupStep = "welcome" | "plugins" | "ai";
export type InstallPhase = "ready" | "installing" | "installed" | "error";
export type InstallRow = { phase: InstallPhase; detail?: string };

const STEPS: Array<{ id: SetupStep; label: string }> = [
  { id: "welcome", label: "Welcome" },
  { id: "plugins", label: "Plugins" },
  { id: "ai", label: "Connect AI" },
];

export interface SetupWizardProps {
  step: SetupStep;
  onStep: (step: SetupStep) => void;
  plugins: StarterPluginDto[];
  rows: Record<string, InstallRow>;
  installing: boolean;
  installError: string;
  onInstall: () => void;
  onClose: (reason: "skip" | "finish") => void;
  /** Walk through a provider's Settings page with its plugin's tour (the setup steps aside). */
  onShowMe: (provider: PluginLlmProvider) => void;
}

/** Store name, description and icon per slug: the cached catalog first, then a fresh read. */
function useStoreItems(): Record<string, DuckyOSStoreItemDto> {
  const index = (items: DuckyOSStoreItemDto[] | undefined) =>
    Object.fromEntries((items || []).filter((i) => i.slug).map((i) => [String(i.slug).toLowerCase(), i]));
  const [items, setItems] = useState(() => index(peekStoreCatalogCache()?.items));
  useEffect(() => {
    let alive = true;
    const api = getApi();
    if (typeof api?.duckyos_store_catalog !== "function") return;
    void Promise.resolve(api.duckyos_store_catalog())
      .then((next) => {
        if (!alive || !next || next.ok === false || !next.items?.length) return;
        rememberStoreCatalog(next);
        setItems(index(next.items));
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);
  return items;
}

function firstSentence(text: string | undefined): string {
  const t = (text || "").trim();
  const m = /^(.+?[.!?])(\s|$)/.exec(t);
  return m ? m[1] : t;
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

function Feature({ icon, tone, title, body }: { icon: ReactNode; tone: string; title: string; body: string }) {
  return (
    <li className="setup-wizard-feature">
      <span className={`setup-wizard-chip is-${tone}`} aria-hidden>
        {icon}
      </span>
      <span>
        <strong>{title}</strong>
        <span className="setup-wizard-muted">{body}</span>
      </span>
    </li>
  );
}

function WelcomeStep() {
  return (
    <>
      <div className="setup-wizard-hero">
        <img className="setup-wizard-logo" src="./OnlineMCPIcon.png" alt="" draggable={false} />
        <h2 id="setup-wizard-title">Welcome to UEFN Ducky</h2>
        <p className="setup-wizard-lead">
          Your AI crew for Fortnite islands. Duckies chat with you, write Verse, build levels, make materials and
          VFX, and work the UEFN editor for you.
        </p>
      </div>
      <ul className="setup-wizard-features">
        <Feature
          icon={<Icons.Duck />}
          tone="amber"
          title="Duckies"
          body="AI helpers you chat with. Each one knows your island and can use UEFN's tools."
        />
        <Feature
          icon={<Icons.Puzzle />}
          tone="purple"
          title="Plugins"
          body="Verse, level design, materials, VFX, animation and more, from the UEFN Ducky Store."
        />
        <Feature
          icon={<Icons.Brain />}
          tone="blue"
          title="Your AI, your choice"
          body="Connect OpenAI, Anthropic or Cursor with an API key, or with a plan you already pay for."
        />
      </ul>
      <p className="setup-wizard-note">Two quick steps: install the starter plugins, then connect an AI.</p>
    </>
  );
}

function StatusChip({ row }: { row: InstallRow | undefined }) {
  const phase = row?.phase ?? "ready";
  if (phase === "installing") {
    return (
      <span className="setup-wizard-status is-installing">
        <Icons.Spinner /> Installing
      </span>
    );
  }
  if (phase === "installed") {
    return (
      <span className="setup-wizard-status is-installed">
        <Icons.Check /> Installed
      </span>
    );
  }
  if (phase === "error") {
    return (
      <span className="setup-wizard-status is-error" title={row?.detail}>
        <Icons.AlertTriangle /> Failed
      </span>
    );
  }
  return <span className="setup-wizard-status">Ready</span>;
}

function PluginRow({
  plugin,
  row,
  item,
}: {
  plugin: StarterPluginDto;
  row: InstallRow | undefined;
  item: DuckyOSStoreItemDto | undefined;
}) {
  const icon = item?.icon_data_url;
  const desc = firstSentence(item?.description);
  return (
    <li className={`setup-wizard-plugin is-${row?.phase ?? "ready"}`}>
      <span className="setup-wizard-plugin-icon" aria-hidden>
        {icon ? <img src={icon} alt="" draggable={false} /> : plugin.group === "gateway" ? <Icons.Brain /> : <Icons.Puzzle />}
      </span>
      <span className="setup-wizard-plugin-text">
        <span className="setup-wizard-plugin-name">{item?.name || plugin.label}</span>
        {desc ? (
          <span className="setup-wizard-plugin-desc" title={desc}>
            {desc}
          </span>
        ) : null}
        {row?.phase === "error" && row.detail ? <span className="setup-wizard-plugin-error">{row.detail}</span> : null}
      </span>
      <StatusChip row={row} />
    </li>
  );
}

function PluginsStep({
  plugins,
  rows,
  installing,
  installError,
}: Pick<SetupWizardProps, "plugins" | "rows" | "installing" | "installError">) {
  const items = useStoreItems();
  const gateways = plugins.filter((p) => p.group === "gateway");
  const editor = plugins.filter((p) => p.group !== "gateway");
  const done = plugins.filter((p) => rows[p.slug]?.phase === "installed").length;
  return (
    <>
      <h2 id="setup-wizard-title">Install the starter plugins</h2>
      <p className="setup-wizard-lead">
        Ducky's features come as plugins. These are the ones most people start with, and they install from the
        UEFN Ducky Store in one go.
      </p>
      <div className="setup-wizard-progress">
        <progress
          className={`setup-wizard-progress-bar${installing ? " is-active" : ""}`}
          value={done}
          max={Math.max(plugins.length, 1)}
          aria-label="Plugins installed"
        />
        <span className="setup-wizard-progress-text">
          {done} of {plugins.length} installed
        </span>
      </div>
      {installError ? <p className="setup-wizard-error">{installError}</p> : null}
      <section className="setup-wizard-group" aria-label="AI gateways">
        <h3>
          AI gateways <span className="setup-wizard-muted">connect Ducky to AI models</span>
        </h3>
        <ul className="setup-wizard-plugin-list">
          {gateways.map((p) => (
            <PluginRow key={p.slug} plugin={p} row={rows[p.slug]} item={items[p.slug]} />
          ))}
        </ul>
      </section>
      <section className="setup-wizard-group" aria-label="UEFN editor tools">
        <h3>
          UEFN editor tools <span className="setup-wizard-muted">what duckies can do in your island</span>
        </h3>
        <ul className="setup-wizard-plugin-list is-grid">
          {editor.map((p) => (
            <PluginRow key={p.slug} plugin={p} row={rows[p.slug]} item={items[p.slug]} />
          ))}
        </ul>
      </section>
    </>
  );
}

type KeyState = { text: string; ok: boolean; pending?: boolean };

function ProviderCard({
  provider,
  agent,
  saved,
  onSaved,
  onShowMe,
}: {
  provider: PluginLlmProvider;
  agent: CodingAgentDto | undefined;
  saved: boolean;
  onSaved: () => void;
  onShowMe: (provider: PluginLlmProvider) => void;
}) {
  const [draft, setDraft] = useState("");
  const [state, setState] = useState<KeyState | null>(null);
  const secretKey = provider.secret_key || provider.id;
  const agentReady = !!agent && agent.available && agent.logged_in !== false;
  const connected = saved || agentReady;

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
      setState({ text: "Key saved. Duckies can use it now.", ok: true });
      onSaved();
    } catch (err) {
      setState({ text: err instanceof Error ? err.message : "Test failed", ok: false });
    }
  };

  let agentLine = "";
  if (agent) {
    if (agentReady) agentLine = `${agent.label} is signed in on this PC.`;
    else if (agent.available) agentLine = `${agent.label} is installed but not signed in.`;
    else agentLine = `Or use ${agent.label}: install it and sign in, no key needed.`;
  }

  return (
    <li className={`setup-wizard-provider${connected ? " is-connected" : ""}`}>
      <div className="setup-wizard-provider-head">
        <span className="setup-wizard-provider-logo" aria-hidden>
          {provider.icon_data_url ? <img src={provider.icon_data_url} alt="" draggable={false} /> : <Icons.Brain />}
        </span>
        <span className="setup-wizard-provider-name">{provider.label}</span>
        <span className={`setup-wizard-pill${connected ? " is-ok" : ""}`}>
          {connected ? (
            <>
              <Icons.Check /> Connected
            </>
          ) : (
            "Not connected"
          )}
        </span>
      </div>
      {provider.kind === "secret" ? (
        <div className="setup-wizard-key-row">
          <input
            type="password"
            className="setup-wizard-input"
            placeholder={saved ? "A key is saved. Paste a new one to replace it" : `Paste your ${provider.label} API key`}
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
      ) : null}
      {state ? <p className={`setup-wizard-key-status${state.ok ? "" : " is-error"}`}>{state.text}</p> : null}
      <div className="setup-wizard-provider-foot">
        <span className="setup-wizard-muted">{agentLine}</span>
        <button type="button" className="setup-wizard-link" onClick={() => onShowMe(provider)}>
          Show me how
        </button>
      </div>
    </li>
  );
}

function AiStep({ onShowMe, onInstallStep }: { onShowMe: SetupWizardProps["onShowMe"]; onInstallStep: () => void }) {
  const contrib = usePluginContributions();
  const [keySaved, setKeySaved] = useState<Record<string, boolean>>({});
  const [agents, setAgents] = useState<CodingAgentDto[]>([]);

  const refresh = useCallback(() => {
    const api = getApi();
    if (!api) return;
    void Promise.resolve(api.get_key_status())
      .then((s) => setKeySaved(s || {}))
      .catch(() => undefined);
    void Promise.resolve(api.list_coding_agents())
      .then((r) => setAgents(r?.agents || []))
      .catch(() => undefined);
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
        .filter((p) => p.chat !== false)
        .slice()
        .sort((a, b) => (a.order ?? 100) - (b.order ?? 100)),
    [contrib.llm_providers],
  );

  return (
    <>
      <h2 id="setup-wizard-title">Connect your AI</h2>
      <p className="setup-wizard-lead">
        Duckies think with AI models. Connect at least one provider. There are two ways to do it:
      </p>
      <div className="setup-wizard-ways">
        <div className="setup-wizard-way">
          <span className="setup-wizard-chip is-amber" aria-hidden>
            <Icons.Key />
          </span>
          <span>
            <strong>API key</strong>
            <span className="setup-wizard-muted">
              Paste a key from the provider's website. What you use is billed to your account with that provider.
            </span>
          </span>
        </div>
        <div className="setup-wizard-way">
          <span className="setup-wizard-chip is-blue" aria-hidden>
            <Icons.Terminal />
          </span>
          <span>
            <strong>Coding agent</strong>
            <span className="setup-wizard-muted">
              Sign in to Claude Code, Codex or Cursor and use the plan you already have. No key needed.
            </span>
          </span>
        </div>
      </div>
      {providers.length ? (
        <ul className="setup-wizard-providers">
          {providers.map((p) => (
            <ProviderCard
              key={p.id}
              provider={p}
              agent={agents.find((a) => (a.plugin_id || "").toLowerCase() === p.plugin_id.toLowerCase())}
              saved={!!keySaved[p.secret_key || p.id]}
              onSaved={refresh}
              onShowMe={onShowMe}
            />
          ))}
        </ul>
      ) : (
        <div className="setup-wizard-empty">
          <p>No AI gateway is installed yet, so there is nothing to connect.</p>
          <button type="button" className="setup-wizard-link" onClick={onInstallStep}>
            Go back and install the plugins
          </button>
        </div>
      )}
      <p className="setup-wizard-note">You can change all of this later in Settings → LLMs.</p>
    </>
  );
}

export function SetupWizard(props: SetupWizardProps) {
  const { step, onStep, plugins, rows, installing, onInstall, onClose } = props;
  const missing = plugins.filter((p) => rows[p.slug]?.phase !== "installed");
  const failed = plugins.some((p) => rows[p.slug]?.phase === "error");
  const done = plugins.length - missing.length;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !installing) onClose("skip");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [installing, onClose]);

  let footer: ReactNode;
  if (step === "welcome") {
    footer = (
      <>
        <button type="button" className="setup-wizard-btn is-ghost" onClick={() => onClose("skip")}>
          Skip for now
        </button>
        <button type="button" className="setup-wizard-btn is-primary" onClick={() => onStep("plugins")}>
          Get started <Icons.ChevronRight />
        </button>
      </>
    );
  } else if (step === "plugins") {
    let primary: ReactNode;
    if (installing) {
      primary = (
        <button type="button" className="setup-wizard-btn is-primary is-busy" disabled>
          <Icons.Spinner /> Installing {Math.min(done + 1, plugins.length)} of {plugins.length}…
        </button>
      );
    } else if (missing.length === 0) {
      primary = (
        <button type="button" className="setup-wizard-btn is-primary" onClick={() => onStep("ai")}>
          Next <Icons.ChevronRight />
        </button>
      );
    } else {
      primary = (
        <button type="button" className="setup-wizard-btn is-primary" onClick={onInstall}>
          <Icons.Download /> {failed ? "Try again" : `Install ${missing.length} plugin${missing.length === 1 ? "" : "s"}`}
        </button>
      );
    }
    footer = (
      <>
        <button type="button" className="setup-wizard-btn is-ghost" disabled={installing} onClick={() => onStep("welcome")}>
          <Icons.ChevronLeft /> Back
        </button>
        <span className="setup-wizard-foot-right">
          {missing.length > 0 && !installing ? (
            <button type="button" className="setup-wizard-btn is-ghost" onClick={() => onStep("ai")}>
              Skip
            </button>
          ) : null}
          {primary}
        </span>
      </>
    );
  } else {
    footer = (
      <>
        <button type="button" className="setup-wizard-btn is-ghost" onClick={() => onStep("plugins")}>
          <Icons.ChevronLeft /> Back
        </button>
        <button type="button" className="setup-wizard-btn is-primary" onClick={() => onClose("finish")}>
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
          <button
            type="button"
            className="setup-wizard-close"
            aria-label="Close setup"
            title="Close setup (it waits in the corner)"
            disabled={installing}
            onClick={() => onClose("skip")}
          >
            <Icons.Close />
          </button>
        </header>
        <div className={`setup-wizard-body is-${step}`}>
          {step === "welcome" ? <WelcomeStep /> : null}
          {step === "plugins" ? (
            <PluginsStep plugins={plugins} rows={rows} installing={installing} installError={props.installError} />
          ) : null}
          {step === "ai" ? <AiStep onShowMe={props.onShowMe} onInstallStep={() => onStep("plugins")} /> : null}
        </div>
        <footer className="setup-wizard-foot">{footer}</footer>
      </section>
    </div>,
    document.body,
  );
}
