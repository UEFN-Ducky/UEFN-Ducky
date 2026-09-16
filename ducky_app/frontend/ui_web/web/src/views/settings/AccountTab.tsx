import { useCallback, useEffect, useRef, useState } from "react";
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

/** Deep link + Log in both call duckyos_login; one in-flight RPC at a time. */
let loginRpcInFlight = false;

function isIgnorableLoginCode(code: string | undefined): boolean {
  return code === "busy" || code === "cancelled";
}

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
    if (next.ok === false && next.error && !isIgnorableLoginCode(next.code)) {
      setError(next.error);
    }
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
    const paired = Boolean(status.device_key_active);
    const id = window.setInterval(() => {
      if (paired) {
        const api = getApi();
        void api?.duckyos_get_status?.().then((s) => {
          if (s) applyStatus(s);
        });
      }
      void refreshRemote();
      void refreshPcs();
    }, starting || paired ? 3000 : 90_000);
    return () => window.clearInterval(id);
  }, [status?.logged_in, status?.device_key_active, remote?.enabled, remote?.running, refreshRemote, refreshPcs, applyStatus]);

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

  const pollRef = useRef(0);
  const loggedInRef = useRef(false);
  loggedInRef.current = Boolean(status?.logged_in);

  const stopLoginUi = useCallback(() => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = 0;
    }
    loginRpcInFlight = false;
    setBusy(false);
    setPairingCode("");
  }, []);

  const handleBrowserLogin = useCallback(() => {
    const api = getApi();
    if (!api?.duckyos_login) return;
    if (loggedInRef.current) return;
    setBusy(true);
    setError("");
    if (!pollRef.current) {
      pollRef.current = window.setInterval(() => {
        void api.duckyos_get_status?.().then((s) => {
          if (!s) return;
          if (s.user_code) setPairingCode(s.user_code);
          if (s.logged_in) {
            applyStatus(s);
            stopLoginUi();
          }
        });
      }, 500);
    }
    if (loginRpcInFlight) return;
    loginRpcInFlight = true;
    void (async () => {
      try {
        const next = await api.duckyos_login(baseUrl.trim() || DEFAULT_BASE);
        if (next.user_code) setPairingCode(next.user_code);
        if (next.logged_in) {
          applyStatus(next);
          stopLoginUi();
          return;
        }
        if (next.code === "cancelled") {
          stopLoginUi();
          return;
        }
        if (next.ok === false && next.error && !isIgnorableLoginCode(next.code)) {
          applyStatus(next);
          setError(next.error);
          stopLoginUi();
          return;
        }
        applyStatus(next);
        // pending / already-in-progress: keep the code + poll until approved or Cancel
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        stopLoginUi();
      }
    })();
  }, [applyStatus, baseUrl, stopLoginUi]);

  useEffect(() => {
    const onLogin = () => {
      consumeAccountLoginRequest();
      handleBrowserLogin();
    };
    window.addEventListener(ACCOUNT_LOGIN_EVENT, onLogin);
    if (consumeAccountLoginRequest()) handleBrowserLogin();
    return () => window.removeEventListener(ACCOUNT_LOGIN_EVENT, onLogin);
  }, [handleBrowserLogin]);

  useEffect(() => {
    return () => {
      if (pollRef.current) {
        window.clearInterval(pollRef.current);
        pollRef.current = 0;
      }
    };
  }, []);

  useEffect(() => {
    if (!loaded || status?.logged_in) return;
    if (status?.browser_pending && status.user_code && !busy && !loginRpcInFlight) {
      handleBrowserLogin();
    }
  }, [loaded, status?.browser_pending, status?.user_code, status?.logged_in, busy, handleBrowserLogin]);

  const handleCancel = () => {
    const api = getApi();
    if (api && typeof api.duckyos_cancel_login === "function") {
      void api.duckyos_cancel_login();
    }
    setError("");
    stopLoginUi();
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
        Passwords never go through UEFN Ducky. Sign in on the website, then connect this PC with a one-time code.
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
        <div className="account-tab-card account-tab-card--login">
          <ol className="account-tab-steps">
            <li>
              Press <strong>Open Profile</strong> — a new browser window opens to your Ducky profile, and this app shows a code.
            </li>
            <li>
              On the site, add this PC and type the code shown here.
            </li>
            <li>This tab updates when the PC is connected.</li>
          </ol>
          {busy ? (
            <div className="account-tab-code-wrap">
              <p className="account-tab-code-label">Code to enter on the site</p>
              {pairingCode ? (
                <p className="account-tab-code" aria-live="polite">
                  {pairingCode}
                </p>
              ) : (
                <p className="account-tab-body">Getting a code…</p>
              )}
            </div>
          ) : null}
          <div className="account-tab-actions">
            <button
              type="button"
              className="account-tab-btn account-tab-btn--primary"
              onClick={() => {
                handleBrowserLogin();
                openSite("/profile");
              }}
            >
              {busy ? "Open Profile again" : "Open Profile"}
            </button>
            {busy ? (
              <button type="button" className="account-tab-btn" onClick={handleCancel}>
                Cancel
              </button>
            ) : null}
          </div>
        </div>
      )}

      {loggedIn ? null : <AgentCapsCard onError={setError} />}
    </div>
  );
}
