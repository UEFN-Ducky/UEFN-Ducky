import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { installPanelPushBus, subscribePanelPush } from "../../hooks/usePanelPushBus";
import { Icons } from "../../icons/Icons";
import { TruncatedText } from "../../components/TruncatedText";
import { Modal, ModalActions } from "../../components/Modal";
import { refreshModelsCatalog } from "../../hooks/modelsCatalogCache";
import { usePluginContributions } from "../../hooks/usePluginContributions";
import type { CodingAgentDto } from "../../types/panel";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { targetRef } from "../../ui-targets/registry";

function BotIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <rect x="3" y="11" width="18" height="10" rx="2" />
      <circle cx="12" cy="5" r="2" />
      <path d="M12 7v4" />
      <circle cx="8" cy="16" r="1" />
      <circle cx="16" cy="16" r="1" />
    </svg>
  );
}

type AgentDraft = {
  enabled: boolean;
  cli_path: string;
  default_args: string;
};

function useCodingAgentsState() {
  const [agents, setAgents] = useState<CodingAgentDto[]>([]);
  const [drafts, setDrafts] = useState<Record<string, AgentDraft>>({});
  const [loaded, setLoaded] = useState(false);

  const refresh = useCallback(async () => {
    const api = getApi();
    if (!api) return;
    const [list, settings] = await Promise.all([api.list_coding_agents(), api.get_settings()]);
    setAgents(list.agents || []);
    const next: Record<string, AgentDraft> = {};
    for (const a of list.agents || []) {
      if (a.id === "ducky") continue;
      const cfg = settings.coding_agents?.[a.id];
      next[a.id] = {
        enabled: cfg?.enabled ?? a.enabled,
        cli_path: cfg?.cli_path ?? a.cli_path ?? "",
        default_args: cfg?.default_args ?? a.default_args ?? "",
      };
    }
    setDrafts(next);
    setLoaded(true);
  }, []);

  useEffect(() => onApiReady(() => void refresh()), [refresh]);

  useEffect(() => {
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type !== "uefn_plugins_changed" && event.type !== "coding_agents_updated") return;
      if (event.type === "uefn_plugins_changed") void refreshModelsCatalog();
      void refresh();
    });
  }, [refresh]);

  const saveAgent = async (id: string, patch: Partial<AgentDraft>) => {
    const api = getApi();
    if (!api) return;
    const current = drafts[id] || { enabled: true, cli_path: "", default_args: "" };
    const merged = { ...current, ...patch };
    setDrafts((d) => ({ ...d, [id]: merged }));
    await api.save_agent_settings({
      coding_agents: { [id]: merged },
    });
    void refresh();
  };

  const setDraft = (id: string, patch: Partial<AgentDraft>) => {
    setDrafts((d) => {
      const current = d[id] || { enabled: true, cli_path: "", default_args: "" };
      return { ...d, [id]: { ...current, ...patch } };
    });
  };

  return { agents, drafts, loaded, refresh, saveAgent, setDraft };
}

function CodingAgentLoginModal({
  agentId,
  title,
  onClose,
  onLoggedIn,
}: {
  agentId: string;
  title: string;
  onClose: () => void;
  onLoggedIn: () => void;
}) {
  const [authUrl, setAuthUrl] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const codeRef = useRef<HTMLInputElement>(null);
  const loggedIn = useRef(false);
  const onLoggedInRef = useRef(onLoggedIn);
  onLoggedInRef.current = onLoggedIn;

  useEffect(() => {
    let cancelled = false;
    const api = getApi();
    if (!api?.coding_agent_login) {
      setStarting(false);
      setError("Login is not available in this Ducky build.");
      return;
    }

    const applyUrl = (url?: string) => {
      const next = String(url || "").trim();
      if (next) setAuthUrl(next);
    };

    void (async () => {
      try {
        const res = await api.coding_agent_login(agentId);
        if (cancelled) return;
        if (res.logged_in) {
          loggedIn.current = true;
          onLoggedInRef.current();
          return;
        }
        if (!res.ok) {
          setError(String(res.error || res.message || "Login failed"));
          return;
        }
        applyUrl(res.auth_url);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setStarting(false);
      }
    })();

    const tick = window.setInterval(() => {
      if (!api.coding_agent_login_status || loggedIn.current) return;
      void (async () => {
        try {
          const st = await api.coding_agent_login_status(agentId);
          if (cancelled || loggedIn.current) return;
          if (st.logged_in) {
            loggedIn.current = true;
            onLoggedInRef.current();
            return;
          }
          applyUrl(st.auth_url);
        } catch {
          /* poll is best-effort */
        }
      })();
    }, 1200);

    return () => {
      cancelled = true;
      window.clearInterval(tick);
    };
  }, [agentId]);

  useEffect(() => {
    if (!starting && !submitting) codeRef.current?.focus();
  }, [starting, submitting]);

  const openLink = () => {
    const url = authUrl.trim();
    if (!url) return;
    const api = getApi();
    if (api?.open_external_url) void api.open_external_url(url);
    else window.open(url, "_blank", "noopener");
  };

  const submit = async () => {
    const api = getApi();
    const token = code.trim();
    if (!api?.coding_agent_login_submit || !token || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const res = await api.coding_agent_login_submit(agentId, token);
      if (res.logged_in) {
        loggedIn.current = true;
        onLoggedInRef.current();
        return;
      }
      setError(String(res.error || res.message || "Claude did not accept that code."));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={title}
      width={480}
      zIndex={100040}
      footer={
        <ModalActions
          onCancel={onClose}
          onConfirm={() => void submit()}
          cancelLabel="Cancel"
          confirmLabel={submitting ? "Submitting…" : "Submit"}
          confirmDisabled={submitting || !code.trim()}
        />
      }
    >
      <p className="general-tab-section-desc" style={{ marginTop: 0 }}>
        Open the link, sign in, then paste the code Claude shows you.
      </p>
      {authUrl ? (
        <button
          type="button"
          className="settings-btn"
          onClick={openLink}
          style={{
            display: "block",
            width: "100%",
            textAlign: "left",
            whiteSpace: "normal",
            wordBreak: "break-all",
            marginBottom: 12,
          }}
        >
          {authUrl}
        </button>
      ) : (
        <p className="general-tab-section-desc">{starting ? "Getting the sign-in link…" : "Waiting for the sign-in link…"}</p>
      )}
      <input
        ref={codeRef}
        className="settings-input"
        type="text"
        autoComplete="off"
        spellCheck={false}
        placeholder="Paste the login code"
        value={code}
        disabled={submitting}
        onChange={(e) => setCode(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            void submit();
          }
        }}
      />
      {error ? (
        <p className="llms-provider-status-text is-fail" style={{ marginTop: 8, marginBottom: 0 }}>
          {error}
        </p>
      ) : null}
    </Modal>
  );
}

function CodingAgentRows({
  rows,
  drafts,
  loaded,
  saveAgent,
  setDraft,
  refresh,
}: {
  rows: CodingAgentDto[];
  drafts: Record<string, AgentDraft>;
  loaded: boolean;
  saveAgent: (id: string, patch: Partial<AgentDraft>) => Promise<void>;
  setDraft: (id: string, patch: Partial<AgentDraft>) => void;
  refresh: () => Promise<void>;
}) {
  const [busyId, setBusyId] = useState("");
  const [detectNote, setDetectNote] = useState<Record<string, string>>({});
  const [loginId, setLoginId] = useState("");
  const [logoutId, setLogoutId] = useState("");

  const closeLogin = useCallback(() => setLoginId(""), []);
  const finishLogin = useCallback(() => {
    setLoginId("");
    void refresh();
    void refreshModelsCatalog();
  }, [refresh]);

  const startLogout = async (agentId: string) => {
    const api = getApi();
    if (!api?.coding_agent_logout) return;
    setLogoutId(agentId);
    setDetectNote((n) => ({ ...n, [agentId]: "Signing out…" }));
    try {
      const res = await api.coding_agent_logout(agentId);
      if (!res.ok) {
        setDetectNote((n) => ({ ...n, [agentId]: String(res.error || res.message || "Logout failed") }));
        return;
      }
      setDetectNote((n) => ({ ...n, [agentId]: res.message || "Logged out." }));
      await refresh();
      void refreshModelsCatalog();
    } catch (e) {
      setDetectNote((n) => ({ ...n, [agentId]: e instanceof Error ? e.message : String(e) }));
    } finally {
      setLogoutId("");
    }
  };

  return (
    <div className="llms-provider-card">
      {rows.map((agent) => {
        const draft = drafts[agent.id] || {
          enabled: agent.enabled,
          cli_path: agent.cli_path || "",
          default_args: agent.default_args || "",
        };
        const isCli = Boolean(agent.capabilities?.needs_cli);
        const help = (agent.install_help || "").trim();
        const note = (detectNote[agent.id] || "").trim();
        const statusText = note || agent.status;
        const detecting = busyId === agent.id;
        const loggingIn = loginId === agent.id;
        const loggingOut = logoutId === agent.id;
        const loggedIn = agent.logged_in === true;
        const needsLogin = agent.available && agent.logged_in === false;
        return (
          <div
            key={agent.id}
            className="llms-provider-row"
            style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}
          >
            <div className="llms-provider-row-main" style={{ width: "100%" }}>
              <div className={`llms-provider-label${agent.available && !needsLogin ? " is-saved" : ""}`}>
                {agent.available && !needsLogin ? (
                  <Icons.Check />
                ) : (
                  <span className="llms-provider-dot" title={needsLogin ? "Not logged in" : "Not available"} />
                )}
                <span>{agent.label}</span>
                {agent.logged_in != null ? (
                  <span
                    className={`llms-provider-status-text ${loggedIn ? "is-ok" : "is-fail"}`}
                    style={{ marginLeft: 8, fontSize: 12 }}
                  >
                    {loggedIn ? "Logged in" : "Not logged in"}
                  </span>
                ) : null}
              </div>
              <div style={{ flex: 1 }} />
              {agent.can_login && (needsLogin || loggingIn) ? (
                <button
                  type="button"
                  className="settings-btn llms-provider-btn"
                  ref={targetRef("settings.llms.provider.agent.login", {
                    kind: "button",
                    label: "Log in",
                    route: "settings.llms",
                  })}
                  disabled={loggingIn || loggingOut || detecting}
                  onClick={() => setLoginId(agent.id)}
                >
                  {loggingIn ? "Logging in…" : "Log in"}
                </button>
              ) : null}
              {agent.can_logout && (loggedIn || loggingOut) ? (
                <button
                  type="button"
                  className="settings-btn llms-provider-btn"
                  ref={targetRef("settings.llms.provider.agent.logout", {
                    kind: "button",
                    label: "Log out",
                    route: "settings.llms",
                  })}
                  disabled={loggingOut || loggingIn || detecting}
                  onClick={() => void startLogout(agent.id)}
                >
                  {loggingOut ? "Signing out…" : "Log out"}
                </button>
              ) : null}
              <label className="general-tab-switch" title="Enable in chat picker">
                <input
                  type="checkbox"
                  className="general-tab-switch-input"
                  checked={draft.enabled}
                  disabled={!loaded || detecting}
                  onChange={(e) => void saveAgent(agent.id, { enabled: e.target.checked })}
                />
                <span className="general-tab-switch-track" aria-hidden />
              </label>
            </div>
            <TruncatedText
              title={statusText}
              className={`llms-provider-status-text ${agent.available && !note ? "is-ok" : "is-fail"}`}
            >
              {detecting ? "Detecting…" : statusText}
            </TruncatedText>
            {!agent.available && help ? (
              <div className="general-tab-section-desc" style={{ marginTop: 0 }}>
                {help}
              </div>
            ) : null}
            {isCli || !agent.available ? (
              <div className="llms-provider-row-main" style={{ width: "100%", gap: 8 }}>
                {isCli ? (
                  <>
                    <input
                      className="settings-input llms-provider-input"
                      type="text"
                      placeholder="CLI path (blank = PATH)"
                      value={draft.cli_path}
                      onChange={(e) => setDraft(agent.id, { cli_path: e.target.value })}
                      onBlur={() => void saveAgent(agent.id, { cli_path: draft.cli_path })}
                    />
                    <input
                      className="settings-input llms-provider-input"
                      type="text"
                      placeholder="Default args"
                      value={draft.default_args}
                      onChange={(e) => setDraft(agent.id, { default_args: e.target.value })}
                      onBlur={() => void saveAgent(agent.id, { default_args: draft.default_args })}
                    />
                  </>
                ) : null}
                <button
                  type="button"
                  className="settings-btn llms-provider-btn"
                  ref={targetRef("settings.llms.provider.agent.detect", {
                    kind: "button",
                    label: "Detect",
                    route: "settings.llms",
                  })}
                  disabled={detecting}
                  onClick={() => {
                    const api = getApi();
                    if (!api) {
                      setDetectNote((n) => ({
                        ...n,
                        [agent.id]: "Panel API not ready — restart Ducky.",
                      }));
                      return;
                    }
                    void (async () => {
                      setBusyId(agent.id);
                      setDetectNote((n) => ({ ...n, [agent.id]: "" }));
                      try {
                        if (!draft.enabled) await saveAgent(agent.id, { enabled: true });
                        const res = api.detect_coding_agent_cli
                          ? await api.detect_coding_agent_cli(agent.id)
                          : null;
                        if (res && res.ok === false) {
                          const err = String(res.error || res.status || "Detect failed").trim();
                          setDetectNote((n) => ({ ...n, [agent.id]: err }));
                        }
                        await refresh();
                        void refreshModelsCatalog();
                      } catch (e) {
                        setDetectNote((n) => ({
                          ...n,
                          [agent.id]: e instanceof Error ? e.message : String(e),
                        }));
                      } finally {
                        setBusyId("");
                      }
                    })();
                  }}
                >
                  {detecting ? "…" : "Detect"}
                </button>
              </div>
            ) : null}
          </div>
        );
      })}
      {loaded && rows.length === 0 ? (
        <div className="general-tab-section-desc">
          No coding agents yet — install Cursor / Anthropic / OpenAI / Google from Settings → Plugins → Gateways.
        </div>
      ) : null}
      {loginId ? (
        <CodingAgentLoginModal
          agentId={loginId}
          title={`Log in to ${rows.find((a) => a.id === loginId)?.label || "Claude Code"}`}
          onClose={closeLogin}
          onLoggedIn={finishLogin}
        />
      ) : null}
    </div>
  );
}

/** Coding-agent controls for one Store gateway (provider detail slide). */
export function ProviderCodingAgents({ pluginId }: { pluginId: string }) {
  const contrib = usePluginContributions();
  const pid = pluginId.trim().toLowerCase();
  const contributed = useMemo(
    () =>
      contrib.llm_coding_agents.filter(
        (a) => (a.plugin_id || "").trim().toLowerCase() === pid && String(a.id || "").trim(),
      ),
    [contrib.llm_coding_agents, pid],
  );
  const { agents, drafts, loaded, refresh, saveAgent, setDraft } = useCodingAgentsState();

  if (!contributed.length) return null;

  const mine: CodingAgentDto[] = contributed.map((row) => {
    const id = String(row.id || "").trim().toLowerCase().replace(/-/g, "_");
    const detected = agents.find(
      (a) => String(a.id || "").trim().toLowerCase().replace(/-/g, "_") === id,
    );
    if (detected) return detected;
    const draft = drafts[id];
    return {
      id,
      label: String(row.label || id).trim() || id,
      enabled: draft?.enabled ?? true,
      available: false,
      status: loaded
        ? "Backend not loaded yet — turn the agent on and click Detect."
        : "Checking…",
      cli_path: draft?.cli_path ?? "",
      default_args: draft?.default_args ?? "",
      capabilities: { needs_cli: true },
      install_help:
        "If this gateway was just installed, wait a moment then Detect — no app restart.",
    };
  });

  return (
    <section
      className="general-tab-section"
      ref={targetRef("settings.llms.provider.agent", {
        kind: "settings_field",
        label: "Coding agent",
        route: "settings.llms",
      })}
    >
      <GeneralSectionHeader icon={<BotIcon />} title="Coding agent" />
      <CodingAgentRows
        rows={mine}
        drafts={drafts}
        loaded={loaded}
        saveAgent={saveAgent}
        setDraft={setDraft}
        refresh={refresh}
      />
    </section>
  );
}
