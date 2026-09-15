import { useCallback, useEffect, useState } from "react";
import { onApiReady } from "../../hooks/onApiReady";
import { getApi } from "../../hooks/usePanelApi";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import type { DuckyOSAccountStatus, RemoteAccessStatus } from "../../types/panel";
import {
  ACCOUNT_LOGIN_EVENT,
  consumeAccountLoginRequest,
} from "../../navigation/deepLinks";
import { DUCKYOS_ACCOUNT_CHANGED } from "../../navigation/openSettingsTab";
import { PluginWalkthroughReplayButton } from "./PluginWalkthroughReplayButton";
import { AgentCapsCard } from "./AgentCapsCard";

const DEFAULT_BASE = "https://uefnducky.org";

export function AccountTab() {
  const { confirm } = useConfirmModal();
  const [status, setStatus] = useState<DuckyOSAccountStatus | null>(null);
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [pairingCode, setPairingCode] = useState("");
  const [remote, setRemote] = useState<RemoteAccessStatus | null>(null);
  const [pcs, setPcs] = useState<Array<{ keyId?: string; name?: string; live?: boolean; mine?: boolean }>>([]);

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

  const refreshPcs = useCallback(async () => {
    const api = getApi();
    if (!api || typeof api.duckyos_list_pcs !== "function") {
      setPcs([]);
      return;
    }
    try {
      const row = await api.duckyos_list_pcs();
      setPcs(Array.isArray(row.devices) ? row.devices : []);
    } catch {
      setPcs([]);
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
      setRemote(null);
      setPcs([]);
      return;
    }
    void refreshRemote();
    void refreshPcs();
    const starting = Boolean(remote?.enabled && !remote?.running);
    const id = window.setInterval(() => {
      void refreshRemote();
      void refreshPcs();
    }, starting ? 3000 : 90_000);
    return () => window.clearInterval(id);
  }, [status?.logged_in, remote?.enabled, remote?.running, refreshRemote, refreshPcs]);

  const run = async (fn: () => Promise<DuckyOSAccountStatus>) => {
    setBusy(true);
    setError("");
    try {
      const next = await fn();
      applyStatus(next);
      if (next.ok === false && next.error) setError(next.error);
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
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        window.clearInterval(poll);
        setPairingCode("");
        setBusy(false);
      }
    })();
  }, [applyStatus, baseUrl]);

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

  const openSite = (path = "/profile") => {
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
  const sessionList =
    remote?.session_list && remote.session_list.length > 0
      ? remote.session_list
      : Array.from({ length: remote?.sessions ?? 0 }, (_, i) => ({
          n: i + 1,
          expires_in_s: 0,
        }));

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
            {pcs.length > 0 ? (
              <ul className="account-tab-pc-list">
                {pcs.map((pc) => (
                  <li key={pc.keyId || pc.name}>
                    <span>
                      {pc.name || "UEFN Ducky"}
                      {pc.mine ? " · this PC" : ""}
                      {pc.live ? " · live" : " · offline"}
                    </span>
                    <button
                      type="button"
                      className="account-tab-btn account-tab-btn--danger"
                      disabled={busy || !pc.keyId}
                      onClick={() => {
                        const keyId = String(pc.keyId || "");
                        if (!keyId) return;
                        void (async () => {
                          const ok = await confirm({
                            title: "Remove this PC?",
                            message: "It will drop off uefnducky.org/ducky until you connect it again.",
                            confirmLabel: "Remove",
                            danger: true,
                          });
                          if (!ok) return;
                          const api = getApi();
                          if (!api?.duckyos_revoke_pc) return;
                          setBusy(true);
                          setError("");
                          try {
                            const next = await api.duckyos_revoke_pc(keyId);
                            if (next.ok === false && next.error) setError(next.error);
                            if (next.logged_in !== undefined) applyStatus(next);
                            await refreshPcs();
                          } catch (err) {
                            setError(err instanceof Error ? err.message : String(err));
                          } finally {
                            setBusy(false);
                          }
                        })();
                      }}
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            <div className="account-tab-actions">
              <button type="button" className="account-tab-btn account-tab-btn--primary" onClick={() => openSite("/profile")}>
                Open Profile in Browser
              </button>
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
              {sessionList.length > 0 ? (
                <button
                  type="button"
                  className="account-tab-btn"
                  disabled={busy}
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
              ) : null}
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

          <AgentCapsCard onError={setError} />
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

      {loggedIn ? null : <AgentCapsCard onError={setError} />}
    </div>
  );
}
