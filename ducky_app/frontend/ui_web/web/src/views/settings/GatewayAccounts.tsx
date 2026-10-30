import { useCallback, useEffect, useState } from "react";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { Icons } from "../../icons/Icons";
import type { GatewayAccountDto, GatewayAccountStatusDto } from "../../types/panel";
import { GeneralSectionHeader } from "./GeneralSectionHeader";

type Kind = "key" | "login";

function resetTime(until: number): string {
  return new Date(until * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function loginText(s: GatewayAccountStatusDto | undefined): string {
  if (!s) return "Checking…";
  if (!s.loggedIn) return "Not logged in";
  return [s.email ? `Logged in as ${s.email}` : "Logged in", s.plan].filter(Boolean).join(" · ");
}

/** Every login / API key of one gateway: add, name, sign in or paste a key, order, pick, remove. */
export function GatewayAccounts({ provider, kind, title }: { provider: string; kind: Kind; title?: string }) {
  const { confirm } = useConfirmModal();
  const [accounts, setAccounts] = useState<GatewayAccountDto[]>([]);
  const [autoSwitch, setAutoSwitch] = useState(true);
  const [statuses, setStatuses] = useState<Record<string, GatewayAccountStatusDto>>({});
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [keySaved, setKeySaved] = useState<Record<string, boolean>>({});
  const [renaming, setRenaming] = useState<{ id: string; label: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    const api = getApi();
    if (!api?.list_gateway_accounts) return;
    const res = await api.list_gateway_accounts(provider);
    if (!res.ok) {
      setNote(res.error);
      return;
    }
    setAccounts(res.accounts);
    setAutoSwitch(res.auto_switch);
    if (kind !== "login" || !api.gateway_account_status) return;
    const pairs = await Promise.all(
      res.accounts.map(async (a) => {
        const s = await api.gateway_account_status!(provider, a.id).catch(() => null);
        return [a.id, s && s.ok ? s.status : { loggedIn: false }] as const;
      }),
    );
    setStatuses(Object.fromEntries(pairs));
  }, [provider, kind]);

  useEffect(() => onApiReady(() => void load()), [load]);

  /** One RPC, then a fresh list; errors land in the status line. */
  const run = async (call: () => Promise<{ ok: boolean; error?: string }> | undefined, done = "") => {
    setBusy(true);
    try {
      const res = await call();
      if (!res) throw new Error("Update Ducky to manage accounts.");
      if (!res.ok) throw new Error(res.error || "Failed");
      setNote(done);
      await load();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const api = () => getApi();
  const add = () =>
    run(() => api()?.add_gateway_account?.(provider, `Account ${accounts.length + 1}`, kind), "Account added.");
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
  const testKey = async (a: GatewayAccountDto) => {
    const value = (keys[a.id] || "").trim();
    if (!value) return;
    setBusy(true);
    setNote("Testing…");
    try {
      const { runBridgeJob } = await import("../../hooks/bridgeJobAsync");
      const res = await runBridgeJob<{ ok: boolean; detail?: string; error?: string }>("test_key", [provider, value], 300_000);
      if (!res.ok) throw new Error(res.detail || res.error || "Key test failed");
      const saved = await api()?.set_gateway_account_key?.(provider, a.id, value);
      if (!saved?.ok) throw new Error(saved && !saved.ok ? saved.error : "Could not save the key");
      setKeys((k) => ({ ...k, [a.id]: "" }));
      setKeySaved((k) => ({ ...k, [a.id]: true }));
      setNote(`${a.label}: key saved.`);
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const group = `gateway-accounts-${provider}`;
  return (
    <section className="general-tab-section gateway-accounts">
      <GeneralSectionHeader icon={<Icons.Users />} title={title || "Accounts"} />
      <div className="llms-provider-card">
        {accounts.map((a, i) => {
          const status = statuses[a.id];
          const detail = [
            a.kind === "login"
              ? loginText(status)
              : a.id === "default"
                ? "Uses the key above"
                : keySaved[a.id]
                  ? "Key saved"
                  : "",
            a.exhausted_until ? `at its limit until ${resetTime(a.exhausted_until)}` : "",
          ].filter(Boolean).join(" · ");
          return (
            <div key={a.id} className="llms-provider-row gateway-accounts-row">
              <div className="llms-provider-row-main">
                <input
                  type="radio"
                  name={group}
                  checked={a.active}
                  disabled={busy}
                  aria-label={`Use ${a.label}`}
                  onChange={() => void run(() => api()?.set_gateway_account?.(provider, a.id))}
                />
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
                  <span className="gateway-accounts-label">{a.label}</span>
                )}
                <span className={`gateway-accounts-detail${a.exhausted_until ? " is-exhausted" : ""}`}>{detail}</span>
                <span className="gateway-accounts-actions">
                  <button type="button" className="settings-btn" disabled={busy || i === 0}
                    aria-label={`Move ${a.label} up`} onClick={() => move(i, -1)}>↑</button>
                  <button type="button" className="settings-btn" disabled={busy || i === accounts.length - 1}
                    aria-label={`Move ${a.label} down`} onClick={() => move(i, 1)}>↓</button>
                  <button type="button" className="settings-btn" disabled={busy}
                    aria-label={`Rename ${a.label}`} onClick={() => setRenaming({ id: a.id, label: a.label })}>Rename</button>
                  {a.kind === "login" ? (
                    status?.loggedIn ? (
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
                  {a.id !== "default" ? (
                    <button type="button" className="settings-btn" disabled={busy}
                      aria-label={`Remove ${a.label}`} onClick={() => void remove(a)}>Remove</button>
                  ) : null}
                </span>
              </div>
              {a.kind === "key" && a.id !== "default" ? (
                <div className="llms-provider-row-main">
                  <input
                    className="settings-input llms-provider-input"
                    type="password"
                    aria-label={`API key for ${a.label}`}
                    placeholder={keySaved[a.id] ? "••••••••••••••••" : "Paste API key"}
                    value={keys[a.id] || ""}
                    onChange={(e) => setKeys((k) => ({ ...k, [a.id]: e.target.value }))}
                  />
                  <button type="button" className="settings-btn llms-provider-btn"
                    disabled={busy || !(keys[a.id] || "").trim()} onClick={() => void testKey(a)}>
                    Test &amp; Save
                  </button>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
      <div className="gateway-accounts-footer">
        <button type="button" className="settings-btn" disabled={busy} onClick={() => void add()}>
          {kind === "login" ? "Add login" : "Add API key"}
        </button>
        <button type="button" className="settings-btn" disabled={busy} onClick={() => void load()}>Refresh</button>
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
      </div>
      <p role="status" className="gateway-accounts-note">{note}</p>
    </section>
  );
}
