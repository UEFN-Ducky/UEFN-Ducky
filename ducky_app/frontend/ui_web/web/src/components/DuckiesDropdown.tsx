import { useCallback, useEffect, useState } from "react";
import { setVisibleInterval } from "../utils/visibleInterval";
import { getApi, isRemote } from "../hooks/usePanelApi";
import type { PluginPeopleSection } from "../types/panel";

export type AccountDucky = { keyId: string; name: string; live: boolean; mine: boolean };

const REFRESH_MS = 60_000;

/** This Ducky first, then the reachable ones, then the rest, each by name. */
export function sortDuckies(rows: Array<Partial<AccountDucky>>): AccountDucky[] {
  return rows
    .filter((r) => r.keyId)
    .map((r) => ({ keyId: String(r.keyId), name: String(r.name || "UEFN Ducky"), live: !!r.live, mine: !!r.mine }))
    .sort((a, b) => Number(b.mine) - Number(a.mine) || Number(b.live) - Number(a.live) || a.name.localeCompare(b.name));
}

/** The other Duckies' line: who you can reach right now. */
export function othersLine(duckies: AccountDucky[]): string {
  const others = duckies.filter((d) => !d.mine);
  const live = others.filter((d) => d.live).length;
  if (!others.length) return "";
  return live ? `${live} of ${others.length} online` : "None online";
}

/** Online right now, besides this Ducky: your other Duckies plus the people plugins list. */
export function onlineCount(duckies: AccountDucky[], sections: PluginPeopleSection[] = []): number {
  const live = duckies.filter((d) => !d.mine && d.live).length;
  return live + sections.reduce((n, s) => n + s.people.filter((p) => p.online).length, 0);
}

/** The header badge: nothing at 0, the number up to 9, then "9+". */
export function badgeText(n: number): string {
  return n <= 0 ? "" : n > 9 ? "9+" : String(n);
}

/**
 * Your account's UEFN Duckies, refreshed every minute and when asked. Only this PC's own
 * windows can open them, so a remote view (a phone, or the window showing another Ducky)
 * lists none.
 */
export function useAccountDuckies(): { duckies: AccountDucky[]; error: string; reload: () => void } {
  const [duckies, setDuckies] = useState<AccountDucky[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const api = getApi();
    if (isRemote() || !api?.ducky_link_list) return;
    try {
      const res = await api.ducky_link_list();
      if (res?.ok === false) {
        setError(String(res.error || ""));
        return;
      }
      setError("");
      setDuckies(sortDuckies(res?.devices ?? []));
    } catch {
      /* signed out or offline: the list stays as it was */
    }
  }, []);

  useEffect(() => {
    void load();
    return setVisibleInterval(() => void load(), REFRESH_MS);
  }, [load]);

  return { duckies, error, reload: useCallback(() => void load(), [load]) };
}

/** The Online tab's "Your UEFN Duckies" rows. Picking one opens it in its own window. */
export function DuckiesRows({ duckies, error, onPicked }: { duckies: AccountDucky[]; error: string; onPicked: () => void }) {
  const others = duckies.filter((d) => !d.mine);
  if (!others.length) return null;
  const me = duckies.find((d) => d.mine);
  const openDucky = (d: AccountDucky) => {
    onPicked();
    void getApi()?.ducky_link_open?.(d.keyId, d.name);
  };
  return (
    <>
      <div className="connection-status-menu-head">Your UEFN Duckies</div>
      {me ? (
        <div className="connection-status-menu-row is-ok">
          <span className="connection-status-menu-dot" aria-hidden />
          <div className="connection-status-menu-text">
            <span className="connection-status-menu-label">{me.name}</span>
            <span className="connection-status-menu-detail">This one · you're here</span>
          </div>
        </div>
      ) : null}
      {others.map((d) => (
        <button
          key={d.keyId}
          type="button"
          className={`connection-status-menu-row duckies-menu-row is-${d.live ? "ok" : "off"}`}
          disabled={!d.live}
          title={d.live ? `Open ${d.name} here` : `${d.name} is offline, or Remote access is off on it`}
          onClick={() => openDucky(d)}
        >
          <span className="connection-status-menu-dot" aria-hidden />
          <div className="connection-status-menu-text">
            <span className="connection-status-menu-label">{d.name}</span>
            <span className="connection-status-menu-detail">{d.live ? "Online · open to talk to it" : "Offline"}</span>
          </div>
        </button>
      ))}
      {error ? <div className="duckies-menu-note">{error}</div> : null}
      <div className="duckies-menu-note">A Ducky shows online while it's open with Remote access on.</div>
    </>
  );
}
