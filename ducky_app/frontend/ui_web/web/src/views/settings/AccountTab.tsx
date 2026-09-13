import { useCallback, useEffect, useState } from "react";
import { onApiReady } from "../../hooks/onApiReady";
import { getApi } from "../../hooks/usePanelApi";
import type {
  DuckyOSAccountStatus,
  DuckyOSTeamDto,
  DuckyOSTeamRoleDto,
  DuckyOSTeamsSnapshot,
} from "../../types/panel";
import {
  ACCOUNT_LOGIN_EVENT,
  consumeAccountLoginRequest,
} from "../../navigation/deepLinks";
import { DUCKYOS_ACCOUNT_CHANGED } from "../../navigation/openSettingsTab";
import { PluginWalkthroughReplayButton } from "./PluginWalkthroughReplayButton";

const DEFAULT_BASE = "https://uefnducky.org";
const NAME_NOT_ALLOWED = "That name isn't allowed.";
const TEAM_PERM_LABELS: Array<{ id: string; label: string }> = [
  { id: "invite", label: "Invite" },
  { id: "revoke_invite", label: "Revoke invite" },
  { id: "change_role", label: "Change role" },
  { id: "remove_member", label: "Remove member" },
  { id: "edit_team", label: "Edit team" },
  { id: "manage_plugins", label: "Manage plugins" },
];
const BLOCKED_NAMES = new Set([
  "ass",
  "asshole",
  "bastard",
  "bitch",
  "cock",
  "crap",
  "cunt",
  "dick",
  "dildo",
  "dyke",
  "fag",
  "faggot",
  "fuck",
  "fuckin",
  "fucking",
  "goddamn",
  "nigga",
  "nigger",
  "penis",
  "piss",
  "pussy",
  "rape",
  "shit",
  "slut",
  "tit",
  "tits",
  "twat",
  "vagina",
  "wank",
  "whore",
]);

function foldLeet(ch: string): string {
  const c = ch.toLowerCase();
  if (c === "0") return "o";
  if (c === "1" || c === "!") return "i";
  if (c === "3") return "e";
  if (c === "4") return "a";
  if (c === "5" || c === "$") return "s";
  if (c === "7") return "t";
  if (c === "@") return "a";
  return c;
}

function nameAllowed(raw: string): boolean {
  let out = "";
  let folded = "";
  let orig = "";
  const flush = () => {
    if (!orig) return;
    out += BLOCKED_NAMES.has(folded) ? "*".repeat([...orig].length) : orig;
    folded = "";
    orig = "";
  };
  for (const c of raw) {
    if (/[0-9A-Za-z@$!]/.test(c)) {
      orig += c;
      folded += foldLeet(c);
      continue;
    }
    flush();
    out += c;
  }
  flush();
  return out === raw;
}

function requireCleanName(raw: string): string {
  const name = raw.trim();
  if (!nameAllowed(name)) throw new Error(NAME_NOT_ALLOWED);
  return name;
}

export function AccountTab() {
  const [status, setStatus] = useState<DuckyOSAccountStatus | null>(null);
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [teams, setTeams] = useState<DuckyOSTeamsSnapshot | null>(null);
  const [teamsLoading, setTeamsLoading] = useState(false);
  const [pairingCode, setPairingCode] = useState("");
  const [capsDenied, setCapsDenied] = useState(0);
  const [remote, setRemote] = useState<{
    enabled?: boolean;
    hostname?: string;
    running?: boolean;
    mode?: string;
    error?: string;
    named_reason?: string;
    site_update_pending?: boolean;
    sessions?: number;
  } | null>(null);
  const [teamTab, setTeamTab] = useState<"team" | "plugins" | "settings">("team");
  const [selectedSlug, setSelectedSlug] = useState("");
  const [createName, setCreateName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [settingsName, setSettingsName] = useState("");
  const [roleDrafts, setRoleDrafts] = useState<DuckyOSTeamRoleDto[]>([]);
  const [teamMsg, setTeamMsg] = useState("");

  const applyStatus = useCallback((next: DuckyOSAccountStatus) => {
    setStatus(next);
    if (next.base_url) setBaseUrl(next.base_url);
    if (next.error) setError(next.error);
    window.dispatchEvent(
      new CustomEvent(DUCKYOS_ACCOUNT_CHANGED, {
        detail: { logged_in: Boolean(next.logged_in) },
      }),
    );
  }, []);

  const refreshRemote = useCallback(async () => {
    const api = getApi();
    if (!api || typeof api.remote_status !== "function") {
      setRemote(null);
      return;
    }
    try {
      setRemote(await api.remote_status());
    } catch {
      setRemote(null);
    }
  }, []);

  const refreshCaps = useCallback(async () => {
    const api = getApi();
    if (!api || typeof api.duckyos_agent_caps !== "function") {
      setCapsDenied(0);
      return;
    }
    try {
      const row = await api.duckyos_agent_caps();
      setCapsDenied(Number(row?.denied_count || 0));
    } catch {
      setCapsDenied(0);
    }
  }, []);

  const refreshTeams = useCallback(async () => {
    const api = getApi();
    if (!api || typeof api.duckyos_teams_snapshot !== "function") {
      setTeams(null);
      return;
    }
    setTeamsLoading(true);
    try {
      const snap = await api.duckyos_teams_snapshot(120);
      setTeams(snap);
    } catch (err) {
      setTeams({
        ok: false,
        error: err instanceof Error ? err.message : String(err),
        needs_team: true,
        teams: [],
        online: [],
      });
    } finally {
      setTeamsLoading(false);
    }
  }, []);

  useEffect(() => {
    const list = teams?.teams || [];
    if (!list.length) {
      setSelectedSlug("");
      setSettingsName("");
      setRoleDrafts([]);
      return;
    }
    const current = list.find((t) => t.slug === selectedSlug) || list[0];
    if (current?.slug && current.slug !== selectedSlug) setSelectedSlug(current.slug);
    setSettingsName(current?.name || "");
    setRoleDrafts(current?.roles?.length ? current.roles.map((r) => ({ ...r, perms: [...(r.perms || [])] })) : []);
    const firstAssignable = (current?.roles || []).find((r) => r.id && r.id !== "owner");
    setInviteRole(firstAssignable?.id || "member");
  }, [teams, selectedSlug]);

  useEffect(() => {
    return onApiReady((api) => {
      void (async () => {
        try {
          const settings = await api.get_settings();
          if (settings?.duckyos_base_url?.trim()) {
            setBaseUrl(settings.duckyos_base_url.trim());
          }
        } catch {
          /* ignore */
        }
        try {
          if (typeof api.duckyos_get_status === "function") {
            applyStatus(await api.duckyos_get_status());
          }
        } catch {
          /* ignore */
        } finally {
          setLoaded(true);
        }
      })();
    });
  }, [applyStatus]);

  useEffect(() => {
    if (!status?.logged_in) {
      setTeams(null);
      setRemote(null);
      setCapsDenied(0);
      return;
    }
    // One initial fetch + slow poll (heartbeat thread covers presence separately).
    void refreshTeams();
    void refreshRemote();
    void refreshCaps();
    const starting = Boolean(remote?.enabled && !remote?.running);
    const id = window.setInterval(() => {
      void refreshTeams();
      void refreshRemote();
    }, starting ? 3000 : 90_000);
    return () => window.clearInterval(id);
  }, [status?.logged_in, remote?.enabled, remote?.running, refreshTeams, refreshRemote, refreshCaps]);

  const run = async (fn: () => Promise<DuckyOSAccountStatus>) => {
    setBusy(true);
    setError("");
    try {
      const next = await fn();
      applyStatus(next);
      if (next.ok === false && next.error) setError(next.error);
      if (next.logged_in) await refreshTeams();
      else setTeams(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleBrowserLogin = useCallback(() => {
    const api = getApi();
    if (!api?.duckyos_login) return;
    setPairingCode("");
    const poll = window.setInterval(() => {
      void api.duckyos_get_status?.().then((s) => {
        if (s?.user_code) setPairingCode(s.user_code);
      });
    }, 500);
    void (async () => {
      setBusy(true);
      setError("");
      try {
        const next = await api.duckyos_login(baseUrl.trim() || DEFAULT_BASE);
        applyStatus(next);
        if (next.ok === false && next.error) setError(next.error);
        if (next.logged_in) await refreshTeams();
        else setTeams(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        window.clearInterval(poll);
        setPairingCode("");
        setBusy(false);
      }
    })();
  }, [applyStatus, baseUrl, refreshTeams]);

  useEffect(() => {
    const onLogin = () => handleBrowserLogin();
    window.addEventListener(ACCOUNT_LOGIN_EVENT, onLogin);
    if (consumeAccountLoginRequest()) handleBrowserLogin();
    return () => window.removeEventListener(ACCOUNT_LOGIN_EVENT, onLogin);
  }, [handleBrowserLogin]);

  const handleCancel = () => {
    const api = getApi();
    if (api && typeof api.duckyos_cancel_login === "function") {
      void api.duckyos_cancel_login();
    }
    setBusy(false);
    setPairingCode("");
    setError("");
  };

  const handleLogout = () => {
    const api = getApi();
    if (!api?.duckyos_logout) return;
    void run(() => api.duckyos_logout());
  };

  const handleOpenAdmin = () => {
    const api = getApi();
    if (api && typeof api.duckyos_open_admin === "function") {
      void api.duckyos_open_admin();
    }
  };

  const runTeam = async (fn: () => Promise<{ ok?: boolean; error?: string } | void>) => {
    setTeamMsg("");
    setBusy(true);
    try {
      const out = await fn();
      if (out && out.ok === false) {
        setTeamMsg(out.error || "Request failed");
        return false;
      }
      await refreshTeams();
      return true;
    } catch (err) {
      setTeamMsg(err instanceof Error ? err.message : String(err));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const openTeamsSite = (path = "/teams") => {
    const api = getApi();
    if (api && typeof api.duckyos_open_teams_site === "function") {
      void api.duckyos_open_teams_site(path);
      return;
    }
    const base = (status?.base_url || baseUrl || DEFAULT_BASE).replace(/\/$/, "");
    window.open(`${base}${path.startsWith("/") ? path : `/${path}`}`, "_blank");
  };

  if (!loaded) {
    return (
      <div className="account-tab">
        <p className="account-tab-muted">Loading account…</p>
      </div>
    );
  }

  const loggedIn = Boolean(status?.logged_in);
  const teamList = teams?.teams || [];
  const needsTeam = Boolean(teams?.needs_team || teamList.length === 0);
  const online = teams?.online || [];

  return (
    <div className="account-tab">
      <h2 className="account-tab-title">
        <span>Ducky Account</span>
        <PluginWalkthroughReplayButton pluginId="account" label="Ducky Account" />
      </h2>
      <p className="account-tab-lead">
        Sign in on uefnducky.org/ducky with the code shown here. Passwords never go through UEFN Ducky.
      </p>

      {error ? <div className="account-tab-error" role="alert">{error}</div> : null}

      {loggedIn ? (
        <>
          <div className="account-tab-card account-tab-card--signed-in">
            <div className="account-tab-signed-row">
              <span className="account-tab-badge">Signed in</span>
              <span className="account-tab-email">{status?.email || "—"}</span>
            </div>
            <p className="account-tab-meta">
              {status?.device_key_active ? (
                <strong className="account-tab-ok">This PC is connected</strong>
              ) : (
                <span className="account-tab-warn">This PC is not connected yet</span>
              )}
            </p>
            <div className="account-tab-actions">
              <button type="button" className="account-tab-btn account-tab-btn--primary" onClick={handleOpenAdmin}>
                Open Admin in browser
              </button>
              <button type="button" className="account-tab-btn" onClick={() => openTeamsSite("/ducky")}>
                Open UEFN Ducky in browser
              </button>
              <button
                type="button"
                className="account-tab-btn account-tab-btn--danger"
                onClick={handleLogout}
                disabled={busy}
              >
                {busy ? "Signing out…" : "Log out"}
              </button>
            </div>
          </div>

          <div className="account-tab-card">
            <div className="account-tab-signed-row">
              <h3 className="account-tab-section-title">UEFN Ducky in the browser</h3>
            </div>
            <p className="account-tab-body">
              Open this PC&apos;s UEFN Ducky in the browser at <code>/ducky</code>. Off by default.
            </p>
            {remote?.site_update_pending ? (
              <p className="account-tab-body account-tab-warn-text">Site update pending</p>
            ) : null}
            {remote?.error && !remote.site_update_pending ? (
              <p className="account-tab-body account-tab-warn-text">{remote.error}</p>
            ) : null}
            <p className="account-tab-meta">
              Tunnel:{" "}
              {remote?.running ? (
                <strong className="account-tab-ok">
                  {remote.mode === "quick" ? "temporary" : remote.mode || "on"}
                </strong>
              ) : remote?.enabled ? (
                <span className="account-tab-warn">starting</span>
              ) : (
                <span className="account-tab-warn">off</span>
              )}
            </p>
            {remote?.hostname ? (
              <p className="account-tab-meta">
                Host: <code>{remote.hostname}</code>
              </p>
            ) : null}
            {remote?.mode === "quick" && remote?.named_reason ? (
              <p className="account-tab-body account-tab-warn-text">
                Own domain unavailable: {remote.named_reason}
              </p>
            ) : null}
            {remote?.mode === "quick" ? (
              <p className="account-tab-meta">
                Temporary address. Your host is <code>u-….uefnducky.org</code> after
                a site token is saved.
              </p>
            ) : null}
            <p className="account-tab-meta">Active sessions: {remote?.sessions ?? 0}</p>
            <div className="account-tab-actions">
              <button
                type="button"
                className="account-tab-btn account-tab-btn--primary"
                disabled={busy}
                onClick={() => {
                  const api = getApi();
                  const setEnabled = api?.remote_set_enabled;
                  if (!setEnabled) return;
                  void (async () => {
                    setBusy(true);
                    try {
                      setRemote(await setEnabled(!remote?.enabled));
                    } catch (err) {
                      setError(err instanceof Error ? err.message : String(err));
                    } finally {
                      setBusy(false);
                    }
                  })();
                }}
              >
                {remote?.enabled ? "Turn off" : "Turn on"}
              </button>
              <button type="button" className="account-tab-btn" onClick={() => openTeamsSite("/ducky")}>
                Open in browser
              </button>
              <button
                type="button"
                className="account-tab-btn"
                disabled={busy || !(remote?.sessions)}
                onClick={() => {
                  const setOut = getApi()?.remote_sign_out_all;
                  if (!setOut) return;
                  void (async () => {
                    setBusy(true);
                    try {
                      setRemote(await setOut());
                    } catch (err) {
                      setError(err instanceof Error ? err.message : String(err));
                    } finally {
                      setBusy(false);
                    }
                  })();
                }}
              >
                Sign out all sessions
              </button>
            </div>
          </div>

          <div className="account-tab-card">
            <h3 className="account-tab-section-title">AI permissions</h3>
            <p className="account-tab-body">
              {capsDenied > 0
                ? `${capsDenied} tool${capsDenied === 1 ? "" : "s"} blocked — change on website.`
                : "What the AI can do is saved on your uefnducky.org account."}
            </p>
            <div className="account-tab-actions">
              <button
                type="button"
                className="account-tab-btn account-tab-btn--primary"
                onClick={() => openTeamsSite("/profile#uefn-ducky")}
              >
                Change on website
              </button>
            </div>
          </div>

          <div className="account-tab-card account-tab-teams">
            <div className="account-tab-signed-row">
              <h3 className="account-tab-section-title">Your team</h3>
              <button
                type="button"
                className="account-tab-btn account-tab-btn--ghost"
                onClick={() => void refreshTeams()}
                disabled={teamsLoading}
              >
                {teamsLoading ? "Refreshing…" : "Refresh"}
              </button>
            </div>

            {teams?.error && teams.ok === false ? (
              <p className="account-tab-body account-tab-warn-text">{teams.error}</p>
            ) : null}
            {teamMsg ? <p className="account-tab-body account-tab-warn-text">{teamMsg}</p> : null}

            {needsTeam ? (
              <>
                {teams?.quota?.can_create === false ? (
                  <p className="account-tab-body">
                    You’ve reached the owned-team limit ({teams.quota.owned_count ?? 0}/
                    {teams.quota.max_owned ?? 1}).
                  </p>
                ) : (
                  <form
                    className="account-tab-form"
                    onSubmit={(ev) => {
                      ev.preventDefault();
                      const api = getApi();
                      if (!api?.duckyos_team_create) return;
                      try {
                        const name = requireCleanName(createName);
                        void runTeam(() => api.duckyos_team_create!(name));
                      } catch (err) {
                        setTeamMsg(err instanceof Error ? err.message : String(err));
                      }
                    }}
                  >
                    <label className="account-tab-field">
                      <span>Team name</span>
                      <input
                        className="account-tab-input"
                        value={createName}
                        maxLength={80}
                        onChange={(e) => setCreateName(e.target.value)}
                        required
                      />
                    </label>
                    <div className="account-tab-actions">
                      <button type="submit" className="account-tab-btn account-tab-btn--primary" disabled={busy}>
                        {busy ? "Creating…" : "Create team"}
                      </button>
                    </div>
                  </form>
                )}
                <p className="account-tab-quiet-link">
                  <button type="button" className="account-tab-btn account-tab-btn--ghost" onClick={() => openTeamsSite("/teams")}>
                    Open on website
                  </button>
                </p>
              </>
            ) : (
              <AccountTeamPanel
                teamList={teamList}
                selectedSlug={selectedSlug}
                setSelectedSlug={setSelectedSlug}
                teamTab={teamTab}
                setTeamTab={setTeamTab}
                online={online}
                inviteEmail={inviteEmail}
                setInviteEmail={setInviteEmail}
                inviteRole={inviteRole}
                setInviteRole={setInviteRole}
                settingsName={settingsName}
                setSettingsName={setSettingsName}
                roleDrafts={roleDrafts}
                setRoleDrafts={setRoleDrafts}
                busy={busy}
                runTeam={runTeam}
                openTeamsSite={openTeamsSite}
                setTeamMsg={setTeamMsg}
              />
            )}
          </div>
        </>
      ) : (
        <div className="account-tab-card">
          {busy ? (
            <>
              {pairingCode ? (
                <>
                  <p className="account-tab-code" aria-live="polite">
                    {pairingCode}
                  </p>
                  <p className="account-tab-body">
                    Open uefnducky.org/ducky and enter this code.
                  </p>
                </>
              ) : (
                <p className="account-tab-body">Getting a code…</p>
              )}
              <div className="account-tab-actions">
                <button type="button" className="account-tab-btn" onClick={handleCancel}>
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <div className="account-tab-actions">
              <button
                type="button"
                className="account-tab-btn account-tab-btn--primary"
                onClick={handleBrowserLogin}
              >
                Log in
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AccountTeamPanel(props: {
  teamList: DuckyOSTeamDto[];
  selectedSlug: string;
  setSelectedSlug: (slug: string) => void;
  teamTab: "team" | "plugins" | "settings";
  setTeamTab: (tab: "team" | "plugins" | "settings") => void;
  online: NonNullable<DuckyOSTeamsSnapshot["online"]>;
  inviteEmail: string;
  setInviteEmail: (v: string) => void;
  inviteRole: string;
  setInviteRole: (v: string) => void;
  settingsName: string;
  setSettingsName: (v: string) => void;
  roleDrafts: DuckyOSTeamRoleDto[];
  setRoleDrafts: (v: DuckyOSTeamRoleDto[]) => void;
  busy: boolean;
  runTeam: (fn: () => Promise<{ ok?: boolean; error?: string } | void>) => Promise<boolean>;
  openTeamsSite: (path?: string) => void;
  setTeamMsg: (v: string) => void;
}) {
  const team = props.teamList.find((t) => t.slug === props.selectedSlug) || props.teamList[0];
  if (!team) return null;
  const perms = team.perms || {};
  const roles = (team.roles || []).filter((r) => r.id && r.id !== "owner");
  const slug = team.slug || "";

  return (
    <>
      {props.teamList.length > 1 ? (
        <label className="account-tab-field">
          <span>Team</span>
          <select
            className="account-tab-input"
            value={slug}
            onChange={(e) => props.setSelectedSlug(e.target.value)}
          >
            {props.teamList.map((t) => (
              <option key={t.slug || t.id} value={t.slug}>
                {t.name || t.slug}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <div className="account-tab-team-head">
          <strong>{team.name || team.slug || "Team"}</strong>
          <span className="account-tab-muted-inline">/{team.slug}</span>
          <span className="account-tab-role">{team.my_role || "member"}</span>
        </div>
      )}

      <div className="account-tab-tabs" role="tablist">
        {(["team", "plugins", "settings"] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            className={`account-tab-tab${props.teamTab === tab ? " is-active" : ""}`}
            onClick={() => props.setTeamTab(tab)}
          >
            {tab === "team" ? "Team" : tab === "plugins" ? "Plugins" : "Settings"}
          </button>
        ))}
      </div>

      {props.teamTab === "team" ? (
        <>
          {perms.invite ? (
            <form
              className="account-tab-form"
              onSubmit={(ev) => {
                ev.preventDefault();
                const api = getApi();
                if (!api?.duckyos_team_invite) return;
                void props.runTeam(() =>
                  api.duckyos_team_invite!(slug, props.inviteEmail, props.inviteRole),
                ).then((ok) => {
                  if (ok) props.setInviteEmail("");
                });
              }}
            >
              <label className="account-tab-field">
                <span>Invite teammate</span>
                <input
                  className="account-tab-input"
                  type="email"
                  value={props.inviteEmail}
                  onChange={(e) => props.setInviteEmail(e.target.value)}
                  required
                />
              </label>
              <label className="account-tab-field">
                <span>Role</span>
                <select
                  className="account-tab-input"
                  value={props.inviteRole}
                  onChange={(e) => props.setInviteRole(e.target.value)}
                >
                  {roles.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name || r.id}
                    </option>
                  ))}
                </select>
              </label>
              <div className="account-tab-actions">
                <button type="submit" className="account-tab-btn account-tab-btn--primary" disabled={props.busy}>
                  Send invite
                </button>
              </div>
            </form>
          ) : null}

          <h4 className="account-tab-subsection-title">Online now</h4>
          {props.online.length === 0 ? (
            <p className="account-tab-muted">No teammates online right now.</p>
          ) : (
            <ul className="account-tab-member-list">
              {props.online.map((row) => (
                <li key={row.user_id || row.display_name}>
                  <span className="account-tab-dot on" />
                  <span className="account-tab-member-name">
                    {row.display_name}
                    {row.is_self ? " (you)" : ""}
                  </span>
                  <span className="account-tab-muted-inline">
                    {[row.source, row.project_label, row.uefn_online ? "UEFN" : ""]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </li>
              ))}
            </ul>
          )}

          <h4 className="account-tab-subsection-title">Members</h4>
          <ul className="account-tab-member-list">
            {(team.members || []).map((m) => (
              <li key={m.user_id || m.email}>
                <span className={`account-tab-dot${m.online ? " on" : ""}`} />
                <span className="account-tab-member-name">{m.display_name || m.email || "member"}</span>
                {perms.change_role && m.role !== "owner" ? (
                  <select
                    className="account-tab-input account-tab-input--inline"
                    value={m.role}
                    disabled={props.busy}
                    onChange={(e) => {
                      const api = getApi();
                      if (!api?.duckyos_team_set_role || !m.user_id) return;
                      void props.runTeam(() =>
                        api.duckyos_team_set_role!(slug, m.user_id || "", e.target.value),
                      );
                    }}
                  >
                    {roles.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name || r.id}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="account-tab-muted-inline">{m.role}</span>
                )}
                {perms.remove_member && m.role !== "owner" ? (
                  <button
                    type="button"
                    className="account-tab-btn account-tab-btn--ghost"
                    disabled={props.busy}
                    onClick={() => {
                      const api = getApi();
                      if (!api?.duckyos_team_remove_member || !m.user_id) return;
                      void props.runTeam(() =>
                        api.duckyos_team_remove_member!(slug, m.user_id || ""),
                      );
                    }}
                  >
                    Remove
                  </button>
                ) : null}
              </li>
            ))}
          </ul>

          {(team.pending_invites || []).length ? (
            <>
              <h4 className="account-tab-subsection-title">Pending invites</h4>
              <ul className="account-tab-member-list">
                {(team.pending_invites || []).map((inv) => (
                  <li key={inv.token || inv.email}>
                    <span className="account-tab-member-name">{inv.email}</span>
                    <span className="account-tab-muted-inline">{inv.role}</span>
                    {perms.revoke_invite && inv.token ? (
                      <button
                        type="button"
                        className="account-tab-btn account-tab-btn--ghost"
                        disabled={props.busy}
                        onClick={() => {
                          const api = getApi();
                          if (!api?.duckyos_team_revoke_invite) return;
                          void props.runTeam(() => api.duckyos_team_revoke_invite!(inv.token || ""));
                        }}
                      >
                        Revoke
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </>
      ) : null}

      {props.teamTab === "plugins" ? (
        <p className="account-tab-body">Community plugin submission is coming soon.</p>
      ) : null}

      {props.teamTab === "settings" ? (
        <>
          {perms.edit_team ? (
            <form
              className="account-tab-form"
              onSubmit={(ev) => {
                ev.preventDefault();
                const api = getApi();
                if (!api?.duckyos_team_update) return;
                try {
                  const name = requireCleanName(props.settingsName);
                  void props.runTeam(() => api.duckyos_team_update!(slug, name));
                } catch (err) {
                  props.setTeamMsg(err instanceof Error ? err.message : String(err));
                }
              }}
            >
              <label className="account-tab-field">
                <span>Name</span>
                <input
                  className="account-tab-input"
                  value={props.settingsName}
                  maxLength={80}
                  onChange={(e) => props.setSettingsName(e.target.value)}
                  required
                />
              </label>
              <div className="account-tab-actions">
                <button type="submit" className="account-tab-btn account-tab-btn--primary" disabled={props.busy}>
                  Save
                </button>
              </div>
            </form>
          ) : (
            <p className="account-tab-muted">You can view this team. Ask an owner to change settings.</p>
          )}

          {perms.manage_roles ? (
            <form
              className="account-tab-form"
              onSubmit={(ev) => {
                ev.preventDefault();
                const api = getApi();
                if (!api?.duckyos_team_set_roles) return;
                try {
                  for (const r of props.roleDrafts) requireCleanName(r.name || r.id || "");
                  void props.runTeam(() => api.duckyos_team_set_roles!(slug, props.roleDrafts));
                } catch (err) {
                  props.setTeamMsg(err instanceof Error ? err.message : String(err));
                }
              }}
            >
              <h4 className="account-tab-subsection-title">Roles</h4>
              {props.roleDrafts.map((role, idx) => (
                <div key={role.id || idx} className="account-tab-role-edit">
                  {role.id === "owner" ? (
                    <p className="account-tab-body">Owner — all permissions. Cannot be changed.</p>
                  ) : (
                    <>
                      <label className="account-tab-field">
                        <span>Name</span>
                        <input
                          className="account-tab-input"
                          value={role.name || ""}
                          maxLength={40}
                          onChange={(e) => {
                            const next = props.roleDrafts.slice();
                            next[idx] = { ...role, name: e.target.value };
                            props.setRoleDrafts(next);
                          }}
                        />
                      </label>
                      <div className="account-tab-perms-grid">
                        {TEAM_PERM_LABELS.map((p) => (
                          <label key={p.id} className="account-tab-perm">
                            <input
                              type="checkbox"
                              checked={(role.perms || []).includes(p.id)}
                              onChange={(e) => {
                                const cur = new Set(role.perms || []);
                                if (e.target.checked) cur.add(p.id);
                                else cur.delete(p.id);
                                const next = props.roleDrafts.slice();
                                next[idx] = { ...role, perms: Array.from(cur) };
                                props.setRoleDrafts(next);
                              }}
                            />
                            <span>{p.label}</span>
                          </label>
                        ))}
                      </div>
                      {!role.builtin ? (
                        <button
                          type="button"
                          className="account-tab-btn account-tab-btn--ghost"
                          onClick={() =>
                            props.setRoleDrafts(props.roleDrafts.filter((_, i) => i !== idx))
                          }
                        >
                          Delete
                        </button>
                      ) : null}
                    </>
                  )}
                </div>
              ))}
              <div className="account-tab-actions">
                <button
                  type="button"
                  className="account-tab-btn"
                  onClick={() =>
                    props.setRoleDrafts([
                      ...props.roleDrafts,
                      { id: "", name: "", builtin: false, perms: [] },
                    ])
                  }
                >
                  Add role
                </button>
                <button type="submit" className="account-tab-btn account-tab-btn--primary" disabled={props.busy}>
                  Save roles
                </button>
              </div>
            </form>
          ) : null}
        </>
      ) : null}

      <p className="account-tab-quiet-link">
        <button type="button" className="account-tab-btn account-tab-btn--ghost" onClick={() => props.openTeamsSite("/teams")}>
          Open on website
        </button>
      </p>
    </>
  );
}
