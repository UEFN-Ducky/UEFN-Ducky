import { useCallback, useEffect, useRef, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import type { AgentCapsSettings } from "../../types/panel";
import { SettingsToggleRow } from "./SettingsToggleRow";

type Caps = Required<AgentCapsSettings>;

const DEFAULTS: Caps = {
  allow_settings_write: true,
  allow_agent_clicks: false,
  allow_see_uefn: true,
  allow_see_other_programs: true,
};

export function AgentCapsCard({ onError }: { onError?: (msg: string) => void }) {
  const [denied, setDenied] = useState<string[]>([]);
  const [caps, setCaps] = useState<Caps>(DEFAULTS);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const saveTimer = useRef<number | null>(null);

  const applyRow = useCallback((row: { denied?: string[]; settings?: AgentCapsSettings }) => {
    setDenied(Array.isArray(row.denied) ? row.denied.map(String) : []);
    const next = row.settings || {};
    setCaps({
      allow_settings_write: next.allow_settings_write !== false,
      allow_agent_clicks: next.allow_agent_clicks === true,
      allow_see_uefn: next.allow_see_uefn !== false,
      allow_see_other_programs: next.allow_see_other_programs !== false,
    });
  }, []);

  const persist = useCallback(
    (nextDenied: string[], nextCaps: Caps) => {
      const setFn = getApi()?.duckyos_agent_caps_set;
      if (!setFn) return;
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => {
        setBusy(true);
        void (async () => {
          try {
            applyRow((await setFn(nextDenied, nextCaps)) || {});
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

  const patch = (partial: Partial<Caps>) => {
    const next = { ...caps, ...partial };
    setCaps(next);
    persist(denied, next);
  };

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

  const locked = !loaded || busy;

  return (
    <div className="account-tab-card account-tab-card--caps">
      <h3 className="account-tab-section-title">AI permissions</h3>
      <p className="account-tab-body">What the AI may change and look at on this PC while it is connected.</p>

      <h4 className="account-tab-subsection-title">What it can change</h4>
      <div className="general-tab-toggle-card">
        <SettingsToggleRow
          id="account-allow-settings-write"
          label="Change Ducky Settings"
          description="Saves Appearance, models, voice, and layout in this app. Not Windows, not other programs."
          checked={caps.allow_settings_write}
          disabled={locked}
          onChange={(on) => patch({ allow_settings_write: on })}
        />
        <SettingsToggleRow
          id="account-allow-agent-clicks"
          label="Click Ducky controls"
          description="On: the AI presses the button it highlighted. Off: it highlights the button and waits for you."
          checked={caps.allow_agent_clicks}
          disabled={locked}
          onChange={(on) => patch({ allow_agent_clicks: on })}
        />
      </div>

      <h4 className="account-tab-subsection-title">What it can see</h4>
      <div className="general-tab-toggle-card">
        <SettingsToggleRow
          id="account-allow-see-uefn"
          label="UEFN"
          description="Editor viewport and screenshots when Unreal Editor for Fortnite is connected."
          checked={caps.allow_see_uefn}
          disabled={locked}
          onChange={(on) => patch({ allow_see_uefn: on })}
        />
        <SettingsToggleRow
          id="account-allow-see-ducky"
          label="Ducky"
          description="This panel. Always on."
          checked
          disabled
          onChange={() => undefined}
        />
        <SettingsToggleRow
          id="account-allow-see-other-programs"
          label="Other programs"
          description="Blender, Unity, Roblox, and screen snips outside UEFN and Ducky."
          checked={caps.allow_see_other_programs}
          disabled={locked}
          onChange={(on) => patch({ allow_see_other_programs: on })}
        />
      </div>
    </div>
  );
}
