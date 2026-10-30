import { useCallback, useEffect, useState } from "react";
import { ContextMenu, type ContextMenuItem } from "../../components/ContextMenu";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { Icons } from "../../icons/Icons";
import type { GatewayAccountDto, GatewayAccountStatusDto } from "../../types/panel";
import type { UsageWindow } from "../../components/GatewayUsageFooter";

type Kind = "key" | "login";

function resetTime(until: number): string {
  return new Date(until * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function loginText(s: GatewayAccountStatusDto | null | undefined): string {
  if (!s) return "Checking…";
  if (!s.loggedIn) return "Not logged in";
  return [s.email || "Logged in", s.plan].filter(Boolean).join(" · ");
}

function keyText(a: GatewayAccountDto): string {
  if (a.id === "default") return "Uses the key above";
  return a.key_hint || (a.has_key ? "Key saved" : "No key yet");
}

/** ★ Default, Full until …, Logged out or No key: the one thing to know about a row. */
function statusPill(a: GatewayAccountDto, isDefault: boolean): { text: string; tone: string } | null {
  if (a.exhausted_until) return { text: `Full until ${resetTime(a.exhausted_until)}`, tone: "is-warn" };
  if (a.kind === "login" && a.status && !a.status.loggedIn) return { text: "Logged out", tone: "is-fail" };
  if (a.kind === "key" && a.id !== "default" && !a.has_key) return { text: "No key", tone: "is-fail" };
  if (isDefault) return { text: "★ Default", tone: "is-ok" };
  return null;
}

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
export function GatewayAccounts({ provider, kind, title }: { provider: string; kind: Kind; title?: string }) {
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

  const load = useCallback(
    async (refresh = false) => {
      const api = getApi();
      if (!api?.list_gateway_accounts) return;
      const res = await api.list_gateway_accounts(provider);
      if (!res.ok) {
        setNote(res.error);
        return;
      }
      setAccounts(res.accounts);
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
    [provider, limits],
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
  const addLogin = async () => {
    const res = await api()?.add_gateway_account?.(provider, `Account ${accounts.length + 1}`, "login");
    if (!res?.ok) return setNote(res && !res.ok ? res.error : "Update Ducky to manage accounts.");
    await run(() => api()?.login_gateway_account?.(provider, res.id), "Finish signing in in the terminal tab, then Refresh.");
  };
  const move = (index: number, delta: number) => {
    const ids = accounts.map((a) => a.id);
    const [id] = ids.splice(index, 1);
    ids.splice(index + delta, 0, id);
    void run(() => api()?.reorder_gateway_accounts?.(provider, ids));
  };
  const remove = async (a: GatewayAccountDto) => {
    const ok = await confirm({
      title: "Remove account",
      message: `Remove ${a.label}? Its ${a.kind === "login" ? "login" : "API key"} is forgotten.`,
      confirmLabel: "Remove",
      danger: true,
    });
    if (ok === true) await run(() => api()?.remove_gateway_account?.(provider, a.id), "Account removed.");
  };
  const saveRename = () => {
    if (!renaming) return;
    const { id, label } = renaming;
    setRenaming(null);
    if (label.trim()) void run(() => api()?.rename_gateway_account?.(provider, id, label.trim()));
  };
  /** Tests the pasted key, then stores it on the row (a new row first for "new"). */
  const saveKey = async () => {
    const value = keyValue.trim();
    if (!value) return;
    setBusy(true);
    setNote("Testing…");
    try {
      const { runBridgeJob } = await import("../../hooks/bridgeJobAsync");
      const test = await runBridgeJob<{ ok: boolean; detail?: string; error?: string }>("test_key", [provider, value], 300_000);
      if (!test.ok) throw new Error(test.detail || test.error || "Key test failed");
      let id = keyFor;
      if (id === "new") {
        const added = await api()?.add_gateway_account?.(provider, `Key ${accounts.length + 1}`, "key");
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
        onKeyDown={(e) => {
          if (e.key === "Enter") void saveKey();
          if (e.key === "Escape") setKeyFor("");
        }}
      />
      <button type="button" className="settings-btn" disabled={busy || !keyValue.trim()} onClick={() => void saveKey()}>
        Save
      </button>
      <button type="button" className="settings-btn" disabled={busy} onClick={() => setKeyFor("")}>
        Cancel
      </button>
    </div>
  );

  const menuItems = (a: GatewayAccountDto, i: number): ContextMenuItem[] => [
    { id: "rename", label: "Rename", onClick: () => setRenaming({ id: a.id, label: a.label }) },
    ...(a.kind === "key" && a.id !== "default"
      ? [{ id: "key", label: "Replace key", onClick: () => { setKeyValue(""); setKeyFor(a.id); } }]
      : []),
    ...(i > 0 ? [{ id: "up", label: "Move up", onClick: () => move(i, -1) }] : []),
    ...(i < accounts.length - 1 ? [{ id: "down", label: "Move down", onClick: () => move(i, 1) }] : []),
    ...(a.id !== "default" ? [{ id: "remove", label: "Remove", danger: true, onClick: () => void remove(a) }] : []),
  ];
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
        <button
          type="button"
          className="settings-btn"
          disabled={busy}
          onClick={() => {
            if (kind === "login") void addLogin();
            else {
              setKeyValue("");
              setKeyFor("new");
            }
          }}
        >
          {kind === "login" ? "Log in another account" : "Add API key"}
        </button>
        <button type="button" className="icon-btn" disabled={busy} aria-label="Refresh" title="Refresh" onClick={() => void load(true)}>
          <Icons.Refresh />
        </button>
      </div>
      {keyFor === "new" ? keyRow : null}
      <div className="llms-provider-card gateway-accounts-card">
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
                <tr key={a.id} className={isDefault ? "is-default" : ""}>
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
                    <div className="gateway-accounts-muted">{a.kind === "login" ? loginText(a.status) : keyText(a)}</div>
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
                        <button type="button" className="settings-btn" disabled={busy}
                          onClick={() => void run(() => api()?.login_gateway_account?.(provider, a.id),
                            "Finish signing in in the terminal tab, then Refresh.")}>
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
