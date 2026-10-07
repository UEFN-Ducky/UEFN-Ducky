import { useEffect, useState } from "react";
import { AiIgnoreSection } from "./AiIgnoreSection";
import { WebAccessSection } from "./AgentTab";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { Icons } from "../../icons/Icons";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { SettingsToggleRow } from "./SettingsToggleRow";
import { savePermissionSettings } from "../../hooks/permissionSettings";
import { SavedPermissionsSection } from "./SavedPermissionsSection";

const ACCESS_CONTROLS = [
  { key: "allow_settings_write", label: "Allow AI settings changes", description: "Let AI tools change supported app settings. File protection rules remain user-only.", defaultValue: true },
  { key: "allow_agent_clicks", label: "Allow AI clicks", description: "Let AI tools activate supported controls in Ducky.", defaultValue: false },
  { key: "allow_see_uefn", label: "Allow AI to see UEFN", description: "Make UEFN viewport and screenshot tools available.", defaultValue: true },
  { key: "allow_see_other_programs", label: "Allow AI to see other programs", description: "Make supported external program and screen capture tools available.", defaultValue: true },
  { key: "terminals_enabled", label: "Allow terminals", description: "Enable terminal panels and supported terminal tools.", defaultValue: true },
] as const;
type AccessKey = typeof ACCESS_CONTROLS[number]["key"];


export function PermissionsTab() {
  const [access, setAccess] = useState<Record<AccessKey, boolean>>({ allow_settings_write: true, allow_agent_clicks: false, allow_see_uefn: true, allow_see_other_programs: true, terminals_enabled: true });
  const [accessLoaded, setAccessLoaded] = useState(false);
  const [savingAccess, setSavingAccess] = useState(false);
  const [accessStatus, setAccessStatus] = useState("");
  useEffect(() => onApiReady(() => {
    void Promise.resolve(getApi()?.get_settings()).then((settings) => {
      if (!settings) throw new Error("Settings unavailable");
      setAccess(Object.fromEntries(ACCESS_CONTROLS.map((control) => [control.key, settings[control.key] ?? control.defaultValue])) as Record<AccessKey, boolean>);
      setAccessLoaded(true);
    }).catch(() => setAccessStatus("Could not load AI access permissions."));
  }), []);
  const saveAccess = async (key: AccessKey, checked: boolean) => {
    const previous = access[key];
    setAccess((current) => ({ ...current, [key]: checked }));
    setSavingAccess(true);
    setAccessStatus("Saving…");
    try {
      await savePermissionSettings({ [key]: checked });
      setAccessStatus("AI access permissions saved.");
    } catch (e) {
      setAccess((current) => ({ ...current, [key]: previous }));
      setAccessStatus(e instanceof Error ? e.message : "Could not save permissions.");
    } finally {
      setSavingAccess(false);
    }
  };
  return (
    <div className="general-tab-shell">
      <h2 className="general-tab-page-title">Permissions and rules</h2>
      <section className="general-tab-section">
        <GeneralSectionHeader icon={<Icons.Settings />} title="AI access" description="Choose what the AI can see and control. Each change saves immediately. Account restrictions and strict file protection take precedence." />
        {ACCESS_CONTROLS.map((control) => <SettingsToggleRow key={control.key} id={control.key} label={control.label} description={control.description}
          checked={access[control.key]} disabled={!accessLoaded || savingAccess}
          onChange={(checked) => { void saveAccess(control.key, checked); }} />)}
        <p role="status">{accessStatus}</p>
      </section>
      <hr className="general-tab-divider" />
      <AiIgnoreSection />
      <hr className="general-tab-divider" />
      <section className="general-tab-section">
        <GeneralSectionHeader icon={<Icons.Globe />} title="Web access" description="Choose whether Ducky may search and read the web." />
        <WebAccessSection />
      </section>
      <hr className="general-tab-divider" />
      <section className="general-tab-section">
        <GeneralSectionHeader icon={<Icons.Settings />} title="Questions and approvals" description="Questions stay pending until you submit an answer. Stop ends the run; it never grants permission." />
        <p>Tool approvals are requested in the owning chat. Allow once applies to that request; saved approvals apply to that chat. Strict file protection still takes precedence.</p>
        <SavedPermissionsSection />
      </section>
    </div>
  );
}
