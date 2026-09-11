import { useCallback, useEffect, useState } from "react";
import { onApiReady } from "../../hooks/onApiReady";
import { getApi } from "../../hooks/usePanelApi";
import type { DuckyOSAccountStatus, DuckyOSTeamsSnapshot } from "../../types/panel";
import { DUCKYOS_ACCOUNT_CHANGED } from "../../navigation/openSettingsTab";
import { PluginWalkthroughReplayButton } from "./PluginWalkthroughReplayButton";

const DEFAULT_BASE = "https://uefnducky.org";

export function AccountTab() {
  const [status, setStatus] = useState<DuckyOSAccountStatus | null>(null);
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [teams, setTeams] = useState<DuckyOSTeamsSnapshot | null>(null);
  const [teamsLoading, setTeamsLoading] = useState(false);
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
      return;
    }
    // One initial fetch + slow poll (heartbeat thread covers presence separately).
    void refreshTeams();
    void refreshRemote();
    const starting = Boolean(remote?.enabled && !remote?.running);
    const id = window.setInterval(() => {
      void refreshTeams();
      void refreshRemote();
    }, starting ? 3000 : 90_000);
    return () => window.clearInterval(id);
  }, [status?.logged_in, remote?.enabled, remote?.running, refreshTeams, refreshRemote]);

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

  const handleBrowserLogin = () => {
    const api = getApi();
    if (!api?.duckyos_login) return;
    void run(() => api.duckyos_login(baseUrl.trim() || DEFAULT_BASE));
  };

  const handleCancel = () => {
    const api = getApi();
    if (api && typeof api.duckyos_cancel_login === "function") {
      void api.duckyos_cancel_login();
    }
    setBusy(false);
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
        Sign in with your browser — if you are already logged into the tenant, you will be sent
        straight back to the app. Passwords never go through UEFN Ducky.
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
              Tenant: <code>{status?.base_url}</code>
            </p>
            <p className="account-tab-meta">
              Device key:{" "}
              {status?.device_key_active ? (
                <strong className="account-tab-ok">active</strong>
              ) : (
                <span className="account-tab-warn">missing</span>
              )}
            </p>
            <div className="account-tab-actions">
              <button type="button" className="account-tab-btn account-tab-btn--primary" onClick={handleOpenAdmin}>
                Open Admin in browser
              </button>
              <button type="button" className="account-tab-btn" onClick={() => openTeamsSite("/ducky")}>
                Open remote in browser
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
              <h3 className="account-tab-section-title">Remote</h3>
            </div>
            <p className="account-tab-body">
              Open this PC&apos;s UEFN Ducky in the browser at <code>/ducky</code>. Traffic stays
              on your machine through Cloudflare. Off by default.
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
                Temporary Cloudflare address. Your host is{" "}
                <code>u-….uefnducky.org</code> after a tunnel token is saved at Admin →
                UEFN Ducky → Remote.
              </p>
            ) : null}
            <p className="account-tab-meta">Active remote sessions: {remote?.sessions ?? 0}</p>
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
                {remote?.enabled ? "Turn remote access off" : "Turn remote access on"}
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
                Sign out all remote
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

            {needsTeam ? (
              <>
                <p className="account-tab-body">
                  You are not on a team yet. Create one on the website — invites, roles, and profiles
                  live there.
                </p>
                <div className="account-tab-actions">
                  <button
                    type="button"
                    className="account-tab-btn account-tab-btn--primary"
                    onClick={() => openTeamsSite("/teams")}
                  >
                    Create a team on website
                  </button>
                </div>
              </>
            ) : (
              <>
                {teamList.map((team) => (
                  <div key={team.id || team.slug} className="account-tab-team">
                    <div className="account-tab-team-head">
                      <strong>{team.name || team.slug || "Team"}</strong>
                      <span className="account-tab-muted-inline">/{team.slug}</span>
                      <span className="account-tab-role">{team.my_role || "member"}</span>
                    </div>
                    <ul className="account-tab-member-list">
                      {(team.members || []).map((m) => (
                        <li key={m.user_id || m.email}>
                          <span className={`account-tab-dot${m.online ? " on" : ""}`} title={m.online ? "Online" : "Offline"} />
                          <span className="account-tab-member-name">{m.display_name || m.email || "member"}</span>
                          <span className="account-tab-muted-inline">{m.role}</span>
                          {m.online && m.presence?.project_label ? (
                            <span className="account-tab-muted-inline">{m.presence.project_label}</span>
                          ) : null}
                          {m.online && m.presence?.uefn_online ? (
                            <span className="account-tab-muted-inline">UEFN</span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}

                <div className="account-tab-online">
                  <h4 className="account-tab-subsection-title">Online now</h4>
                  {online.length === 0 ? (
                    <p className="account-tab-muted">No teammates online right now.</p>
                  ) : (
                    <ul className="account-tab-member-list">
                      {online.map((row) => (
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
                </div>

                <div className="account-tab-actions">
                  <button
                    type="button"
                    className="account-tab-btn account-tab-btn--primary"
                    onClick={() => openTeamsSite("/teams")}
                  >
                    Manage team on website
                  </button>
                  <button type="button" className="account-tab-btn" onClick={() => openTeamsSite("/invite")}>
                    Invite page
                  </button>
                </div>
              </>
            )}
          </div>
        </>
      ) : (
        <div className="account-tab-card">
          <label className="account-tab-label" htmlFor="duckyos-base">
            Tenant URL
          </label>
          <input
            id="duckyos-base"
            className="account-tab-input"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            disabled={busy}
            placeholder={DEFAULT_BASE}
            autoComplete="url"
          />
          {busy ? (
            <>
              <p className="account-tab-body">
                Waiting for browser… Finish signing in there (or cancel).
              </p>
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
                Sign in with browser
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
