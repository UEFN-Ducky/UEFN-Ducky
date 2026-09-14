import { useCallback, useEffect, useRef, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { SettingsToggleRow } from "./SettingsToggleRow";

export function AgentCapsCard({ onError }: { onError?: (msg: string) => void }) {
  const [denied, setDenied] = useState<string[]>([]);
  const [writeOn, setWriteOn] = useState(true);
  const [clicksOn, setClicksOn] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const saveTimer = useRef<number | null>(null);

  const applyRow = useCallback((row: {
    denied?: string[];
    settings?: { allow_settings_write?: boolean; allow_agent_clicks?: boolean };
  }) => {
    setDenied(Array.isArray(row.denied) ? row.denied.map(String) : []);
    setWriteOn(row.settings?.allow_settings_write !== false);
    setClicksOn(row.settings?.allow_agent_clicks === true);
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
          applyRow(await getCaps(false));
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

  return (
    <div className="account-tab-card account-tab-card--caps">
      <h3 className="account-tab-section-title">AI permissions</h3>
      <div className="general-tab-toggle-card">
        <SettingsToggleRow
          id="account-allow-settings-write"
          label="Let the AI change Settings on this PC."
          checked={writeOn}
          disabled={!loaded || busy}
          onChange={(on) => {
            setWriteOn(on);
            persist(denied, on, clicksOn);
          }}
        />
        <SettingsToggleRow
          id="account-allow-agent-clicks"
          label="Let the AI click panel controls."
          description="Off = it only spotlights and you click."
          checked={clicksOn}
          disabled={!loaded || busy}
          onChange={(on) => {
            setClicksOn(on);
            persist(denied, writeOn, on);
          }}
        />
      </div>
    </div>
  );
}
