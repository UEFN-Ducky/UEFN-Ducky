import { runBridgeJob } from "../../hooks/bridgeJobAsync";
import { useCallback, useEffect, useRef, useState } from "react";
import { ContextMenu, type ContextMenuItem } from "../../components/ContextMenu";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { getApi, isRemote } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { Icons } from "../../icons/Icons";
import type { GatewayAccountDto, GatewayAccountStatusDto } from "../../types/panel";
import type { UsageWindow } from "../../components/GatewayUsageFooter";
import { targetRef } from "../../ui-targets/registry";
import { BROWSER_WAIT, INSTALLING, REPAIRING, waitForInstall as waitForCliInstall, waitForSignIn } from "./gatewayLogin";

/** Walkthroughs' "paste a key" and "Test & Save" steps land on the API keys table. */
const ADD_KEY_TARGET = { kind: "button", label: "Add API key", route: "settings.llms" } as const;
const SAVE_KEY_TARGET = { kind: "button", label: "Test & Save", route: "settings.llms" } as const;

type Kind = "key" | "login";

function resetTime(until: number): string {
  return new Date(until * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function loginText(s: GatewayAccountStatusDto | null | undefined): string {
  if (!s) return "Checking…";
  if (s.pending) return s.installing ? INSTALLING : s.repairing ? REPAIRING : BROWSER_WAIT;
  if (!s.loggedIn) return s.error || "Not logged in";
  if (s.access === "none") return [s.email, s.error || "No access for this account"].filter(Boolean).join(" · ");
  return [s.email || "Logged in", s.plan].filter(Boolean).join(" · ");
}

function keyText(a: GatewayAccountDto): string {
  return a.has_key ? a.key_hint || "Key saved" : "No key yet";
}

/** ★ Default, Full until …, Logged out or No key: the one thing to know about a row. */
function statusPill(a: GatewayAccountDto, isDefault: boolean): { text: string; tone: string } | null {
  if (a.exhausted_until) return { text: `Full until ${resetTime(a.exhausted_until)}`, tone: "is-warn" };
  if (a.kind === "login" && a.status && !a.status.loggedIn) return { text: "Logged out", tone: "is-fail" };
  if (a.kind === "login" && a.status?.access === "none") return { text: "No access", tone: "is-fail" };
  if (a.kind === "key" && !a.has_key) return { text: "No key", tone: "is-fail" };
  if (isDefault) return { text: "★ Default", tone: "is-ok" };
  return null;
}

const LEAVE_MS = 180;
const TERMINAL_HINT = "Finish signing in in the terminal tab, then Refresh.";
const reducedMotion = () =>
  typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;

function UsageBars({ windows }: { windows: UsageWindow[] | undefined }) {
  const rows = (windows || []).filter((w) => Number(w.limit) > 0);
  if (!windows) return <span className="gateway-accounts-muted">…</span>;
  if (!rows.length) return <span className="gateway-accounts-muted">—</span>;
  return (
    <div className="gateway-accounts-usage">
      {rows.map((w) => {
        const used = Math.max(0, Math.min(100, (Number(w.used) / Number(w.limit)) * 100));
        const left = Math.round(100 - used);
        return (
          <div key={w.id || w.label} className="gateway-accounts-bar" title={w.label}>
            <div className="gateway-accounts-bar-top">
              <span>{w.label}</span>
              <span>{left}% left</span>
            </div>
            <div className={`gateway-accounts-track${left <= 1 ? " is-max" : left <= 20 ? " is-hot" : ""}`}>
              <i style={{ width: `${used}%` }} />
            </div>
            {w.reset ? <div className="gateway-accounts-muted">{w.reset}</div> : null}
          </div>
        );
      })}
    </div>
  );
}

/** One gateway's API keys or CLI logins as a table: status and default for new chats.
 *  Usage left and switching at a limit belong to subscription logins (Claude Code, Codex);
 *  API keys are billed per token, so their table has neither. */
/** Opens a provider's key page in the person's browser (the window can't navigate away). */
function openKeyPage(url: string) {
  const api = getApi();
  if (!isRemote() && api?.open_external_url) void api.open_external_url(url);
  else window.open(url, "_blank", "noopener");
}

export function GatewayAccounts({ provider, kind, title, keyUrl }: {
  provider: string; kind: Kind; title?: string; keyUrl?: string;
}) {
  const { confirm } = useConfirmModal();
  const [accounts, setAccounts] = useState<GatewayAccountDto[]>([]);
  const [defaultId, setDefaultId] = useState("");
  const [autoSwitch, setAutoSwitch] = useState(true);
  const [usage, setUsage] = useState<Record<string, UsageWindow[]>>({});
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const limits = kind === "login";
  // Row whose key is being typed: "new" = the Add API key row.
  const [keyFor, setKeyFor] = useState("");
  const [keyValue, setKeyValue] = useState("");
  const [renaming, setRenaming] = useState<{ id: string; label: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [loaded, setLoaded] = useState(false);
  // Row fading out while its delete runs.
  const [leaving, setLeaving] = useState("");
  // Row signing in hidden in the browser, and what it says meanwhile.
  const [waiting, setWaiting] = useState<{ id: string; text: string } | null>(null);
  const mounted = useRef(true);
  useEffect(() => () => void (mounted.current = false), []);

  const load = useCallback(
    async (refresh = false) => {
      const api = getApi();
      if (!api?.list_gateway_accounts) return;
      const res = await api.list_gateway_accounts(provider);
      if (!res.ok) {
        setNote(res.error);
        return;
      }
      // The table's kind wins: older cores reported a lone CLI login (Default) as a key.
      setAccounts(res.accounts.map((a) => ({ ...a, kind })));
      setLoaded(true);
      setDefaultId(res.default || res.accounts.find((a) => a.active)?.id || "");
      setAutoSwitch(res.auto_switch);
      if (!limits || !api.gateway_account_usage) return;
      for (const a of res.accounts) {
        void api
          .gateway_account_usage(provider, a.id, refresh)
          .then((u) => setUsage((m) => ({ ...m, [a.id]: u.ok ? u.windows || [] : [] })))
          .catch(() => setUsage((m) => ({ ...m, [a.id]: [] })));
      }
    },
    [provider, limits, kind],
  );

  useEffect(() => onApiReady(() => void load()), [load]);

  /** One RPC, then a fresh list; errors land in the status line. */
  const run = async (call: () => Promise<{ ok: boolean; error?: string }> | undefined, done = "") => {
    setBusy(true);
    setMenu(null);
    try {
      const res = await call();
      if (!res) throw new Error("Update Ducky to manage accounts.");
      if (!res.ok) throw new Error(res.error || "Failed");
      setNote(done);
      await load();
      return true;
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const api = () => getApi();
  const makeDefault = (a: GatewayAccountDto) => {
    setDefaultId(a.id);
    void run(() => api()?.set_gateway_account?.(provider, a.id), `${a.label} is the default for new chats.`);
  };
  /** True once a CLI install the plugin is running for this row has finished cleanly. */
  const waitForInstall = async (id: string): Promise<boolean> => {
    try {
      const install = await waitForCliInstall(provider, id, {
        alive: () => mounted.current,
        onWaiting: (text) => {
          setNote("");
          setWaiting({ id, text });
        },
      });
      if (install.error) setNote(install.error);
      return install.ok;
    } finally {
      if (mounted.current) setWaiting(null);
    }
  };
  /** Log in: a terminal tab (most CLIs), or a hidden browser sign-in whose status we poll. */
  /** False when the sign-in didn't start (the CLI missing, an RPC error). */
  const startLogin = async (id: string, retried = false): Promise<boolean> => {
    setBusy(true);
    setMenu(null);
    let started: { ok: boolean; error?: string; waiting?: boolean; message?: string } | undefined;
    try {
      started = await api()?.login_gateway_account?.(provider, id);
      if (!started) throw new Error("Update Ducky to manage accounts.");
      if (!started.ok) throw new Error(started.error || "Failed");
      setNote(started.waiting ? "" : TERMINAL_HINT);
      await load();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
      started = undefined;
    } finally {
      setBusy(false);
    }
    if (!started?.ok) {
      // The CLI isn't installed: Log in started its install. Wait for it here and sign in
      // by itself once, so the person never has to press Log in a second time.
      if (!retried && (await waitForInstall(id))) return startLogin(id, true);
      return false;
    }
    if (!started.waiting) return true;
    const done = await waitForSignIn(provider, id, started.message || BROWSER_WAIT, {
      alive: () => mounted.current,
      onWaiting: (text) => setWaiting({ id, text }),
    });
    if (!mounted.current) return true;
    setWaiting(null);
    setNote(done.text);
    await load();
    return true;
  };
  const addLogin = async () => {
    const names = new Set(accounts.map((a) => a.label));
    let n = accounts.length + 1;
    while (names.has(`Account ${n}`)) n += 1;
    const res = await api()?.add_gateway_account?.(provider, `Account ${n}`, "login");
    if (!res?.ok) return setNote(res && !res.ok ? res.error : "Update Ducky to manage accounts.");
    // A sign-in that couldn't start leaves no empty row behind (three "Account 2" rows
    // piled up while Claude Code wasn't installed).
    if (!(await startLogin(res.id))) {
      await api()?.remove_gateway_account?.(provider, res.id);
      await load();
    }
  };
  const move = (index: number, delta: number) => {
    const ids = accounts.map((a) => a.id);
    const [id] = ids.splice(index, 1);
    ids.splice(index + delta, 0, id);
    void run(() => api()?.reorder_gateway_accounts?.(provider, ids));
  };
  const remove = async (a: GatewayAccountDto) => {
    const ok = await confirm({
      title: "Delete account",
      message: `Delete ${a.label}? Its saved ${a.kind === "login" ? "login" : "key"} is removed from this PC.`,
      confirmLabel: "Delete",
      danger: true,
    });
    if (ok !== true) return;
    if (!reducedMotion()) {
      setLeaving(a.id);
      await new Promise((r) => setTimeout(r, LEAVE_MS));
    }
    await run(() => api()?.remove_gateway_account?.(provider, a.id), `${a.label} deleted.`);
    setLeaving("");
  };
  const saveRename = () => {
    if (!renaming) return;
    const { id, label } = renaming;
    setRenaming(null);
    if (label.trim()) void run(() => api()?.rename_gateway_account?.(provider, id, label.trim()));
  };
  const openKeyRow = (id: string) => {
    setKeyValue("");
    setKeyFor(id);
  };
  /** Tests the pasted key against the provider's own API, then stores it on the row (a new
   *  row first for "new"). No base URL to fill: keys work out of the box. A row that
   *  already had one (older Ducky) keeps it and is tested against it. */
  const saveKey = async () => {
    const value = keyValue.trim();
    if (!value) return;
    const url = accounts.find((a) => a.id === keyFor)?.base_url || "";
    setBusy(true);
    setNote(""); // the button says Testing…; a second line under the table only repeated it
    try {
      const test = await runBridgeJob<{ ok: boolean; detail?: string; error?: string }>(
        "test_key", [provider, value, url, true], 300_000);
      if (!test.ok) throw new Error(test.detail || test.error || "Key test failed");
      let id = keyFor;
      // An empty key row (Default with no key) takes the key: a new "Key 2" beside it left
      // chats on the keyless Default and two rows named alike in the model picker.
      const empty = id === "new" ? accounts.find((a) => a.kind === "key" && !a.has_key) : undefined;
      if (empty) id = empty.id;
      if (id === "new") {
        const names = new Set(accounts.map((a) => a.label));
        let n = accounts.length + 1;
        while (names.has(`Key ${n}`)) n += 1;
        const added = await api()?.add_gateway_account?.(provider, `Key ${n}`, "key");
        if (!added?.ok) throw new Error(added && !added.ok ? added.error : "Could not add the key");
        id = added.id;
      }
      const saved = await api()?.set_gateway_account_key?.(provider, id, value);
      if (!saved?.ok) throw new Error(saved && !saved.ok ? saved.error : "Could not save the key");
      setKeyFor("");
      setKeyValue("");
      setNote("Key saved.");
      await load();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const keys = (e: { key: string }) => {
    if (e.key === "Enter") void saveKey();
    if (e.key === "Escape") setKeyFor("");
  };
  const keyRow = (
    <div className="gateway-accounts-keyrow">
      <input
        className="settings-input llms-provider-input"
        type="password"
        autoFocus
        aria-label="API key"
        placeholder="Paste API key"
        value={keyValue}
        onChange={(e) => setKeyValue(e.target.value)}
        onKeyDown={keys}
      />
      <button
        type="button"
        className="settings-btn"
        ref={kind === "key" ? targetRef("settings.llms.provider.save", SAVE_KEY_TARGET) : undefined}
        disabled={busy || !keyValue.trim()}
        onClick={() => void saveKey()}
      >
        {busy ? "Testing…" : "Test & Save"}
      </button>
      <button type="button" className="settings-btn" disabled={busy} onClick={() => setKeyFor("")}>
        Cancel
      </button>
    </div>
  );

  const menuItems = (a: GatewayAccountDto, i: number): ContextMenuItem[] => [
    { id: "rename", label: "Rename", onClick: () => setRenaming({ id: a.id, label: a.label }) },
    ...(a.kind === "key"
      ? [{ id: "key", label: a.has_key ? "Replace key" : "Add key", onClick: () => openKeyRow(a.id) }]
      : []),
    ...(i > 0 ? [{ id: "up", label: "Move up", onClick: () => move(i, -1) }] : []),
    ...(i < accounts.length - 1 ? [{ id: "down", label: "Move down", onClick: () => move(i, 1) }] : []),
    { id: "remove", label: "Delete", danger: true, onClick: () => void remove(a) },
  ];
  const addFirst = () => (kind === "login" ? void addLogin() : openKeyRow("new"));
  const loginName = (title || "").replace(/\s*logins?$/i, "").trim();
  const menuIndex = menu ? accounts.findIndex((a) => a.id === menu.id) : -1;

  const heading = title || "Accounts";
  return (
    <section className="general-tab-section gateway-accounts" aria-label={heading}>
      <div className="gateway-accounts-head">
        <h3 className="general-tab-section-title">
          <span className="general-tab-section-icon" aria-hidden>
            <Icons.Users />
          </span>
          <span className="general-tab-section-title-text">{`${heading} (${accounts.length})`}</span>
        </h3>
        {kind === "key" && keyUrl && accounts.length > 0 && !accounts.some((a) => a.has_key) ? (
          <button type="button" className="link-btn gateway-accounts-keylink" onClick={() => openKeyPage(keyUrl)}>
            Get a key
          </button>
        ) : null}
        {loaded && !accounts.length ? null : (
          <button
            type="button"
            className="settings-btn"
            ref={kind === "key" ? targetRef("settings.llms.provider.key", ADD_KEY_TARGET) : undefined}
            disabled={busy}
            onClick={addFirst}
          >
            {kind === "login" ? "Log in another account" : "Add API key"}
          </button>
        )}
        <button type="button" className="icon-btn" disabled={busy} aria-label="Refresh" title="Refresh" onClick={() => void load(true)}>
          <Icons.Refresh />
        </button>
      </div>
      {keyFor === "new" ? keyRow : null}
      {loaded && !accounts.length ? (
        <div className="llms-provider-card gateway-accounts-empty" role="status">
          <span className="gateway-accounts-empty-icon" aria-hidden>
            <Icons.Users />
          </span>
          <div className="gateway-accounts-label">
            {kind === "login" ? `No ${loginName ? `${loginName} ` : ""}login yet` : "No API key yet"}
          </div>
          <div className="gateway-accounts-muted">Add one to use these models in chats.</div>
          {kind === "key" && keyUrl ? (
            <button type="button" className="link-btn gateway-accounts-keylink" onClick={() => openKeyPage(keyUrl)}>
              Get a key
            </button>
          ) : null}
          {keyFor === "new" ? null : (
            <button
              type="button"
              className="settings-btn"
              ref={kind === "key" ? targetRef("settings.llms.provider.key", ADD_KEY_TARGET) : undefined}
              disabled={busy}
              onClick={addFirst}
            >
              {kind === "login" ? "Log in" : "Add API key"}
            </button>
          )}
        </div>
      ) : null}
      <div className="llms-provider-card gateway-accounts-card" hidden={loaded && !accounts.length}>
        <table className="gateway-accounts-table">
          <thead>
            <tr>
              <th>Account</th>
              {limits ? <th>Usage</th> : null}
              <th>Status</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {accounts.map((a) => {
              const isDefault = a.id === defaultId;
              const pill = statusPill(a, isDefault);
              return (
                <tr key={a.id} className={[isDefault ? "is-default" : "", leaving === a.id ? "is-leaving" : ""].join(" ").trim()}>
                  <td>
                    {renaming?.id === a.id ? (
                      <input
                        className="settings-input"
                        autoFocus
                        aria-label={`Name for ${a.label}`}
                        value={renaming.label}
                        onChange={(e) => setRenaming({ id: a.id, label: e.target.value })}
                        onBlur={saveRename}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") saveRename();
                          if (e.key === "Escape") setRenaming(null);
                        }}
                      />
                    ) : (
                      <div className="gateway-accounts-label">{a.label}</div>
                    )}
                    <div className="gateway-accounts-muted">
                      {waiting?.id === a.id ? (
                        <span className="gateway-accounts-waiting">
                          <Icons.Spinner />
                          {waiting.text}
                        </span>
                      ) : a.kind === "login" ? loginText(a.status) : keyText(a)}
                    </div>
                    {keyFor === a.id ? keyRow : null}
                  </td>
                  {limits ? (
                    <td>
                      <UsageBars windows={usage[a.id]} />
                    </td>
                  ) : null}
                  <td>{pill ? <span className={`coding-agent-pill gateway-accounts-pill ${pill.tone}`}>{pill.text}</span> : null}</td>
                  <td className="gateway-accounts-actions">
                    {!isDefault ? (
                      <button type="button" className="settings-btn" disabled={busy}
                        aria-label={`Make ${a.label} default`} onClick={() => makeDefault(a)}>
                        Make default
                      </button>
                    ) : null}
                    {a.kind === "login" ? (
                      a.status?.loggedIn ? (
                        <button type="button" className="settings-btn" disabled={busy}
                          onClick={() => void run(() => api()?.logout_gateway_account?.(provider, a.id), `${a.label} logged out.`)}>
                          Log out
                        </button>
                      ) : (
                        <button type="button" className="settings-btn" disabled={busy || waiting?.id === a.id}
                          onClick={() => void startLogin(a.id)}>
                          Log in
                        </button>
                      )
                    ) : null}
                    <button type="button" className="icon-btn" disabled={busy} aria-haspopup="menu"
                      aria-expanded={menu?.id === a.id} aria-label={`More for ${a.label}`} title="More"
                      onClick={(e) => {
                        const r = e.currentTarget.getBoundingClientRect();
                        setMenu({ id: a.id, x: r.right - 220, y: r.bottom + 4 });
                      }}>
                      <Icons.MoreHorizontal />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {menu && menuIndex >= 0 ? (
        <ContextMenu x={menu.x} y={menu.y} items={menuItems(accounts[menuIndex], menuIndex)} onClose={() => setMenu(null)} />
      ) : null}
      {accounts.length === 1 ? (
        <p className="gateway-accounts-muted">
          {limits
            ? "Log in another account to run two chats on two accounts, or to switch when one is full."
            : "Add another API key to run two chats on two keys."}
        </p>
      ) : null}
      {limits ? (
        <label className="gateway-accounts-auto">
          <input
            type="checkbox"
            checked={autoSwitch}
            disabled={busy}
            onChange={(e) => {
              const on = e.target.checked;
              void run(() => api()?.set_gateway_auto_switch?.(provider, on));
            }}
          />
          Switch accounts when one is at its limit
        </label>
      ) : null}
      <p role="status" className="gateway-accounts-note">{note}</p>
    </section>
  );
}
