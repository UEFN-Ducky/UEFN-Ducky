import { useCallback, useEffect, useRef, useState } from "react";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { getApi } from "../hooks/usePanelApi";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import type { PluginScopeChoice, PluginScopeStatus } from "../types/panel";
import { setVisibleInterval } from "../utils/visibleInterval";
import { lostText, storageLine, switchConfirm, syncText } from "./scopeBarText";

/** Team pulls: on open, on focus, and each minute while a team-scoped panel is open.
 * The host also caps it at one call per team per minute. */
const SYNC_EVERY_MS = 60_000;

/** Help text (plan §13, same words as the web). */
const DATA_AT_REST ="Plugin data on each PC is encrypted for the signed-in account.";

type Props = {
  pluginId: string;
  /** The plugin's data or scope changed: the pane tells the iframe to re-read. */
  onScopeChanged: (status: PluginScopeStatus) => void;
  /** False while the tab is kept but hidden: no minute sync for a panel nobody sees. */
  active?: boolean;
};

/** Whose data: account + scope. Any change means the plugin must re-read. */
const scopeKey = (s: PluginScopeStatus | null) =>
  s ? `${s.email}:${s.scope?.kind}:${s.scope?.teamId}:${s.scope?.readOnly}` : "";

/**
 * Host-rendered bar above every plugin panel: whose data this is (plan §8). The
 * plugin can't hide or restyle it. Renders nothing for accounts without the Teams
 * beta (rule 13: normal members see no change).
 */
export function PluginScopeBar({ pluginId, onScopeChanged, active = true }: Props) {
  const { confirm } = useConfirmModal();
  const [status, setStatus] = useState<PluginScopeStatus | null>(null);
  const [menu, setMenu] = useState<PluginScopeChoice[] | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const notify = useRef(onScopeChanged);
  notify.current = onScopeChanged;
  const last = useRef<PluginScopeStatus | null>(null);

  /** Re-read the bar; tell the iframe when the scope moved or its data changed. */
  const refresh = useCallback(async (dataChanged = false) => {
    const api = getApi();
    if (!api?.plugin_scope_status) return;
    try {
      const next = await api.plugin_scope_status(pluginId);
      const moved = last.current !== null && scopeKey(next) !== scopeKey(last.current);
      last.current = next;
      setStatus(next);
      setNow(Date.now());
      if (moved || dataChanged) notify.current(next);
    } catch {
      /* keep the last bar */
    }
  }, [pluginId]);

  useEffect(() => {
    void refresh();
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type === "plugin_scope_changed") {
        // A sync round with no changes for this plugin only refreshes the bar.
        const plugins = event.plugins ?? [];
        void refresh(plugins.includes(pluginId) || (!event.synced && plugins.length === 0));
      } else if (event.type === "duckyos_account_changed") {
        void refresh();
      }
    });
  }, [refresh, pluginId]);

  const teamId = status?.visible && status.scope?.kind === "team" ? status.scope.teamId : "";
  useEffect(() => {
    if (!teamId || !active) return;
    const sync = () => void getApi()?.plugin_scope_sync?.(pluginId, false);
    sync();
    window.addEventListener("focus", sync);
    const stop = setVisibleInterval(() => {
      sync();
      setNow(Date.now());
    }, SYNC_EVERY_MS);
    return () => {
      window.removeEventListener("focus", sync);
      stop();
    };
  }, [teamId, pluginId, active]);

  if (!status?.visible || !status.scope) return null;
  const scope = status.scope;
  const team = scope.kind === "team";
  const current = team ? scope.teamId : "personal";
  const storage = team ? storageLine(status.usage) : null;

  const toggleMenu = async () => {
    if (menuOpen) {
      setMenuOpen(false);
      return;
    }
    setMenuOpen(true);
    setMenu(null);
    if (!status.canChange) return;
    const res = await getApi()?.plugin_scope_choices?.();
    setMenu(res?.choices ?? []);
  };

  const choose = async (choice: PluginScopeChoice) => {
    setMenuOpen(false);
    if (choice.id === current) return;
    const ok = await confirm({ ...switchConfirm(status.pluginLabel || pluginId, choice), confirmLabel: "Switch" });
    if (ok !== true) return;
    const next = await getApi()?.plugin_scope_set?.(pluginId, choice.id);
    if (next?.ok === false && next.error) {
      setStatus({ ...status, error: next.error });
      return;
    }
    await refresh();
  };

  const waiting = team && status.state === "waiting";
  const locked = status.state === "locked";
  const lost = team && status.state === "lost";
  const variant = team ? (scope.readOnly && !waiting && !locked ? "paused" : "team") : "personal";
  return (
    <div
      className={`plugin-scope-bar plugin-scope-bar--${variant}`}
      role="status"
      aria-label={team ? `Plugin data: team ${scope.label}` : "Plugin data: local"}
      title={DATA_AT_REST}
    >
      <span
        className={`plugin-scope-bar__badge plugin-scope-bar__badge--${team ? "team" : "local"}`}
        title={team ? `Team data: ${scope.label}` : "Local data: only on this PC"}
      >
        {team ? scope.label : "Local"}
      </span>
      {locked ? (
        <span className="plugin-scope-bar__text">
          Waiting for your account&apos;s data key. Read-only until you&apos;re back online.
        </span>
      ) : waiting ? (
        <span className="plugin-scope-bar__text">Waiting for team data. Read-only until it arrives.</span>
      ) : lost ? (
        <span className="plugin-scope-bar__text">{lostText(scope.label, status.deleteAt)}</span>
      ) : team && scope.readOnly ? (
        <>
          <span className="plugin-scope-bar__text">Team Private paused. Read-only.</span>
          <button
            type="button"
            className="plugin-scope-bar__link"
            onClick={() => void getApi()?.duckyos_open_teams_site?.("/profile")}
          >
            Ask the owner to renew
          </button>
        </>
      ) : (
        <>
          {status.email ? <span className="plugin-scope-bar__text">signed in as {status.email}</span> : null}
          {team ? (
            <>
              {status.members ? (
                <span className="plugin-scope-bar__text">
                  shared with {status.members} member{status.members === 1 ? "" : "s"}
                </span>
              ) : null}
              <span className="plugin-scope-bar__text">{syncText(status, now)}</span>
              {storage ? (
                <span className={`plugin-scope-bar__storage plugin-scope-bar__storage--${storage.level}`}>
                  <progress
                    className="plugin-scope-bar__meter"
                    value={storage.pct}
                    max={100}
                    aria-label="Team storage used"
                  />
                  <span>{storage.text}</span>
                  {storage.note ? <strong>{storage.note}</strong> : null}
                </span>
              ) : null}
            </>
          ) : (
            <span className="plugin-scope-bar__text">only on this PC · {DATA_AT_REST}</span>
          )}
        </>
      )}
      {status.error && status.state !== "offline" ? (
        <span className="plugin-scope-bar__text plugin-scope-bar__text--error">{status.error}</span>
      ) : null}
      <span className="plugin-scope-bar__change">
        <button
          type="button"
          className="plugin-scope-bar__link"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => void toggleMenu()}
        >
          Change ▾
        </button>
        {menuOpen ? (
          <span className="plugin-scope-bar__menu" role="menu">
            {!status.canChange ? (
              <span className="plugin-scope-bar__menu-note">Open a project to choose whose data it shows.</span>
            ) : menu === null ? (
              <span className="plugin-scope-bar__menu-note">Loading…</span>
            ) : (
              menu.map((choice) => (
                <button
                  key={choice.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={choice.id === current}
                  className="plugin-scope-bar__menu-item"
                  onClick={() => void choose(choice)}
                >
                  {choice.kind === "team" ? `Team · ${choice.label}` : "Local"}
                </button>
              ))
            )}
          </span>
        ) : null}
      </span>
    </div>
  );
}
