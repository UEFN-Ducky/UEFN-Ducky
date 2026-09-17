import { useCallback, useEffect, useRef, useState } from "react";
import { onApiReady } from "../../hooks/onApiReady";
import { getApi } from "../../hooks/usePanelApi";
import type { DuckyOSAccountStatus, RemoteAccessStatus } from "../../types/panel";
import {
  ACCOUNT_LOGIN_EVENT,
  consumeAccountLoginRequest,
} from "../../navigation/deepLinks";
import { DUCKYOS_ACCOUNT_CHANGED } from "../../navigation/openSettingsTab";
import { PluginWalkthroughReplayButton } from "./PluginWalkthroughReplayButton";

const DEFAULT_BASE = "https://uefnducky.org";

/** Deep link + Log in both call duckyos_login; one in-flight RPC at a time. */
let loginRpcInFlight = false;

function isIgnorableLoginCode(code: string | undefined): boolean {
  return code === "busy" || code === "cancelled";
}

export function AccountTab() {
  const [status, setStatus] = useState<DuckyOSAccountStatus | null>(null);
  const [baseUrl, setBaseUrl] = useState(DEFAULT_BASE);
  const [busy, setBusy] = useState(false);
  const [accountAction, setAccountAction] = useState<"toggle" | "logout" | "disconnect" | null>(null);
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
  const pairingCodeRef = useRef("");
  pairingCodeRef.current = pairingCode;
  const pendingProfileOpenRef = useRef(false);

  const stopLoginUi = useCallback(() => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = 0;
    }
    loginRpcInFlight = false;
    pendingProfileOpenRef.current = false;
    setBusy(false);
    setPairingCode("");
  }, []);

  const openProfilePair = useCallback(
    (code: string) => {
      const c = String(code || "").trim();
      if (!c) return;
      const path = `/profile?pair=${encodeURIComponent(c)}`;
      const api = getApi();
      if (api && typeof api.duckyos_open_teams_site === "function") {
        void api.duckyos_open_teams_site(path);
        return;
      }
      const base = (status?.base_url || baseUrl || DEFAULT_BASE).replace(/\/$/, "");
      window.open(`${base}${path}`, "_blank");
    },
    [baseUrl, status?.base_url],
  );

  const offerPairCode = useCallback(
    (code: string) => {
      const c = String(code || "").trim();
      if (!c) return;
      setPairingCode(c);
      if (!pendingProfileOpenRef.current) return;
      pendingProfileOpenRef.current = false;
      openProfilePair(c);
    },
    [openProfilePair],
  );

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
          if (s.user_code) offerPairCode(s.user_code);
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
        if (next.user_code) offerPairCode(next.user_code);
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
  }, [applyStatus, baseUrl, offerPairCode, stopLoginUi]);

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
    setAccountAction("logout");
    void run(() => api.duckyos_logout()).finally(() => setAccountAction(null));
  };

  if (!loaded) {
    return (
      <div className="account-tab">
        <p className="account-tab-muted">Loading account…</p>
      </div>
    );
  }

  const loggedIn = Boolean(status?.logged_in);
  const displayName = status?.display_name?.trim() || status?.email?.trim() || "Ducky account";
  const thisPc = pcs.find((pc) => pc.mine);
  const sessions = remote?.session_list ?? Array.from(
    { length: remote?.sessions ?? 0 },
    (_, i) => ({ n: i + 1, expires_in_s: 0 }),
  );
  const accessState = !status?.device_key_active
    ? "Not connected"
    : !remote
      ? "Checking connection…"
      : remote.error
        ? "Connection needs attention"
        : !remote.enabled
          ? "Disabled"
          : remote.running
            ? "Enabled"
            : "Connecting…";
  const accessDescription = !status?.device_key_active
    ? "Log out and sign in again to connect this PC."
    : !remote
      ? "Checking browser access for this PC."
      : remote.error
        ? remote.error
        : !remote.enabled
          ? "Browser access is paused. Your account stays signed in."
          : "Access this PC from your Ducky account in the browser.";

  const handleToggle = async () => {
    const api = getApi();
    if (!api?.remote_set_enabled || !remote) return;
    setBusy(true);
    setAccountAction("toggle");
    setError("");
    try {
      const next = await api.remote_set_enabled(!remote.enabled);
      if (next.ok === false) {
        setError(next.error || "Could not update browser access. Try again.");
      } else {
        setRemote(next);
        if (next.error) setError(next.error);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      setAccountAction(null);
    }
  };

  const handleDisconnectSessions = async () => {
    const api = getApi();
    if (!api?.remote_sign_out_all) return;
    setBusy(true);
    setAccountAction("disconnect");
    setError("");
    try {
      const next = await api.remote_sign_out_all();
      if (next.ok === false) {
        setError(next.error || "Could not disconnect sessions. Try again.");
      } else {
        setRemote(next);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      setAccountAction(null);
    }
  };

  return (
    <div className="account-tab">
      <h2 className="account-tab-title">
        <span>Ducky Account</span>
        {!loggedIn && <PluginWalkthroughReplayButton pluginId="account" label="Ducky Account" />}
      </h2>
      {!loggedIn && (
        <p className="account-tab-lead">
          Sign in on the website, then connect this PC with a one-time code.
          Your password stays on the website.
        </p>
      )}

      {error ? <div className="account-tab-error" role="alert">{error}</div> : null}

      {loggedIn ? (
        <section className="account-tab-card account-tab-card--signed-in" aria-label="Connected account">
          <div className="account-tab-identity">
            <div className="account-tab-avatar" aria-hidden="true">
              {Array.from(displayName)[0]?.toLocaleUpperCase()}
            </div>
            <div className="account-tab-identity-text">
              <span className="account-tab-eyebrow">Signed in as</span>
              <h3 className="account-tab-name">{displayName}</h3>
              {status?.email && status.email !== displayName && (
                <p className="account-tab-account-email">{status.email}</p>
              )}
            </div>
          </div>
          <div className="account-tab-connection">
            <div className="account-tab-connection-heading">
              <div>
                <span className="account-tab-eyebrow">Browser access · This PC</span>
                {thisPc?.name && <p className="account-tab-device-name">{thisPc.name}</p>}
              </div>
              <span className={`account-tab-badge${remote?.enabled && remote.running && !remote.error && status?.device_key_active ? "" : " account-tab-badge--muted"}`} role="status">
                {accessState}
              </span>
            </div>
            <p className="account-tab-body">{accessDescription}</p>
          </div>
          <div className="account-tab-actions">
            <button
              type="button"
              className="account-tab-btn account-tab-btn--primary"
              disabled={busy || !remote || !status?.device_key_active}
              onClick={() => void handleToggle()}
            >
              {accountAction === "toggle" ? (remote?.enabled ? "Disabling…" : "Enabling…") : (remote?.enabled ? "Disable" : "Enable")}
            </button>
            <button
              type="button"
              className="account-tab-btn account-tab-btn--danger"
              onClick={handleLogout}
              disabled={busy}
            >
              {accountAction === "logout" ? "Logging out…" : "Log out"}
            </button>
          </div>
          <p className="account-tab-meta">Log out disconnects this PC and removes the account from this app.</p>
          <section className="account-tab-sessions" aria-label="Browser sessions">
            <div className="account-tab-connection-heading">
              <h3 className="account-tab-sessions-title">Browser sessions</h3>
              {remote && <span className="account-tab-meta" role="status">{sessions.length} active</span>}
            </div>
            <p className="account-tab-meta">
              Disconnect browser sessions to this PC. Your account stays signed in here.
            </p>
            {sessions.length > 0 ? (
              <ul className="account-tab-session-list">
                {sessions.map((session) => (
                  <li key={session.n}>
                    <span>Browser session {session.n}</span>
                    {session.expires_in_s > 0 && (
                      <span>Expires in {Math.ceil(session.expires_in_s / 60)} min</span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="account-tab-meta">{remote ? "No active browser sessions." : "Session details unavailable."}</p>
            )}
            <button
              type="button"
              className="account-tab-btn"
              disabled={busy || !remote || sessions.length === 0}
              onClick={() => void handleDisconnectSessions()}
            >
              {accountAction === "disconnect" ? "Disconnecting…" : "Disconnect all sessions"}
            </button>
          </section>
        </section>
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
                const existing = pairingCodeRef.current.trim();
                if (existing) {
                  pendingProfileOpenRef.current = false;
                  handleBrowserLogin();
                  openProfilePair(existing);
                  return;
                }
                pendingProfileOpenRef.current = true;
                handleBrowserLogin();
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
    </div>
  );
}
