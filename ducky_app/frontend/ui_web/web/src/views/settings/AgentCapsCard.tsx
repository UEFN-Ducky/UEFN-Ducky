import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { SettingsToggleRow } from "./SettingsToggleRow";

type CapsTool = { name?: string; description?: string; destructive?: boolean };
type CapsCategory = { id?: string; label?: string; tools?: CapsTool[] };

export function AgentCapsCard({ onError }: { onError?: (msg: string) => void }) {
  const [denied, setDenied] = useState<string[]>([]);
  const [writeOn, setWriteOn] = useState(true);
  const [clicksOn, setClicksOn] = useState(false);
  const [categories, setCategories] = useState<CapsCategory[]>([]);
  const [filter, setFilter] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const saveTimer = useRef<number | null>(null);

  const applyRow = useCallback((row: {
    denied?: string[];
    settings?: { allow_settings_write?: boolean; allow_agent_clicks?: boolean };
    catalog?: { categories?: CapsCategory[] };
  }) => {
    setDenied(Array.isArray(row.denied) ? row.denied.map(String) : []);
    setWriteOn(row.settings?.allow_settings_write !== false);
    setClicksOn(row.settings?.allow_agent_clicks === true);
    if (Array.isArray(row.catalog?.categories)) setCategories(row.catalog.categories);
  }, []);

  const persist = useCallback(
    (nextDenied: string[], nextWrite: boolean, nextClicks: boolean) => {
      const setCaps = getApi()?.duckyos_agent_caps_set;
      if (!setCaps) return;
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => {
        setBusy(true);
        void (async () => {
          try {
            const row = await setCaps(nextDenied, {
              allow_settings_write: nextWrite,
              allow_agent_clicks: nextClicks,
            });
            applyRow(row || {});
          } catch (err) {
            onError?.(err instanceof Error ? err.message : String(err));
          } finally {
            setBusy(false);
          }
        })();
      }, 200);
    },
    [applyRow, onError],
  );

  useEffect(() => {
    return onApiReady((api) => {
      const getCaps = api.duckyos_agent_caps;
      if (!getCaps) {
        setLoaded(true);
        return;
      }
      void (async () => {
        try {
          applyRow(await getCaps(true));
        } catch (err) {
          onError?.(err instanceof Error ? err.message : String(err));
        } finally {
          setLoaded(true);
        }
      })();
    });
  }, [applyRow, onError]);

  useEffect(() => () => {
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
  }, []);

  const deniedSet = useMemo(() => new Set(denied), [denied]);
  const q = filter.trim().toLowerCase();

  const toggleTool = (name: string, allow: boolean) => {
    const next = allow ? denied.filter((n) => n !== name) : [...denied.filter((n) => n !== name), name];
    setDenied(next);
    persist(next, writeOn, clicksOn);
  };

  const toggleGroup = (tools: CapsTool[], allow: boolean) => {
    const names = tools.map((t) => String(t.name || "")).filter(Boolean);
    const drop = new Set(names);
    const next = allow
      ? denied.filter((n) => !drop.has(n))
      : [...denied.filter((n) => !drop.has(n)), ...names.filter((n) => !deniedSet.has(n))];
    setDenied(next);
    persist(next, writeOn, clicksOn);
  };

  const blocked = denied.length;

  return (
    <div className="account-tab-card account-tab-card--caps">
      <h3 className="account-tab-section-title">AI permissions</h3>
      <p className="account-tab-body">
        What this PC&apos;s AI can do. Changes apply here immediately
        {blocked ? ` — ${blocked} tool${blocked === 1 ? "" : "s"} blocked.` : "."}
      </p>
      <div className="general-tab-toggle-card">
        <SettingsToggleRow
          id="account-allow-settings-write"
          label="Allow agent settings writes"
          description="Let the AI change Settings on this PC."
          checked={writeOn}
          disabled={!loaded || busy}
          onChange={(on) => {
            setWriteOn(on);
            persist(denied, on, clicksOn);
          }}
        />
        <SettingsToggleRow
          id="account-allow-agent-clicks"
          label="Allow agent clicks"
          description="Let the AI click panel controls. Off = it only spotlights and you click."
          checked={clicksOn}
          disabled={!loaded || busy}
          onChange={(on) => {
            setClicksOn(on);
            persist(denied, writeOn, on);
          }}
        />
      </div>
      <label className="account-tab-field">
        <span>Tools</span>
        <input
          className="account-tab-input"
          type="search"
          value={filter}
          placeholder="Filter tools"
          onChange={(e) => setFilter(e.target.value)}
        />
      </label>
      {!loaded ? (
        <p className="account-tab-muted">Loading tools from this PC…</p>
      ) : categories.length === 0 ? (
        <p className="account-tab-muted">No tools loaded yet. Open a chat once, then come back.</p>
      ) : (
        <div className="account-tab-caps-list">
          {categories.map((cat) => {
            const tools = (cat.tools || []).filter((t) => t.name);
            const visible = q
              ? tools.filter((t) => {
                  const text = `${t.name} ${t.description || ""} ${cat.label || ""}`.toLowerCase();
                  return text.includes(q);
                })
              : tools;
            if (!visible.length) return null;
            const allowed = visible.filter((t) => !deniedSet.has(String(t.name))).length;
            const id = String(cat.id || cat.label || "group");
            return (
              <details key={id} className="account-tab-caps-group" open>
                <summary>
                  <label className="account-tab-caps-master" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={allowed === visible.length}
                      onChange={(e) => toggleGroup(visible, e.target.checked)}
                    />
                    <span>
                      {cat.label || id}{" "}
                      <small>
                        {allowed}/{visible.length}
                      </small>
                    </span>
                  </label>
                </summary>
                {visible.map((tool) => {
                  const name = String(tool.name);
                  return (
                    <label
                      key={name}
                      className={`account-tab-caps-tool${tool.destructive ? " account-tab-caps-tool--destructive" : ""}`}
                    >
                      <input
                        type="checkbox"
                        checked={!deniedSet.has(name)}
                        onChange={(e) => toggleTool(name, e.target.checked)}
                      />
                      <span>
                        <strong>{name}</strong>
                        {tool.description ? <small>{tool.description}</small> : null}
                      </span>
                    </label>
                  );
                })}
              </details>
            );
          })}
        </div>
      )}
    </div>
  );
}
