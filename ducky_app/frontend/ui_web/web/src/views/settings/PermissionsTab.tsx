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
  const [usageStats, setUsageStats] = useState(true);
  const [usageLoaded, setUsageLoaded] = useState(false);
  const [savingUsage, setSavingUsage] = useState(false);
  const [usageStatus, setUsageStatus] = useState("");
  const [allowAll, setAllowAll] = useState(false);
  const [savingAllowAll, setSavingAllowAll] = useState(false);
  const [allowAllStatus, setAllowAllStatus] = useState("");
  useEffect(() => onApiReady(() => {
    void Promise.resolve(getApi()?.get_settings()).then((settings) => {
      if (!settings) throw new Error("Settings unavailable");
      setAccess(Object.fromEntries(ACCESS_CONTROLS.map((control) => [control.key, settings[control.key] ?? control.defaultValue])) as Record<AccessKey, boolean>);
      setAllowAll(settings.allow_everything_all_chats ?? false);
      setAccessLoaded(true);
      setUsageStats(settings.usage_stats ?? true);
      setUsageLoaded(true);
    }).catch(() => setAccessStatus("Could not load AI access permissions."));
  }), []);
  const saveUsage = async (checked: boolean) => {
    const previous = usageStats;
    setUsageStats(checked);
    setSavingUsage(true);
    setUsageStatus("Saving…");
    try {
      await savePermissionSettings({ usage_stats: checked });
      setUsageStatus(checked ? "Anonymous usage stats on." : "Anonymous usage stats off. Nothing queued, nothing sent.");
    } catch (e) {
      setUsageStats(previous);
      setUsageStatus(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSavingUsage(false);
    }
  };
  const saveAllowAll = async (checked: boolean) => {
    setAllowAll(checked);
    setSavingAllowAll(true);
    setAllowAllStatus("Saving…");
    try {
      await savePermissionSettings({ allow_everything_all_chats: checked });
      setAllowAllStatus(checked ? "Every chat allows everything. Questions still come to you." : "Each chat uses its own permissions again.");
    } catch (e) {
      setAllowAll(!checked);
      setAllowAllStatus(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSavingAllowAll(false);
    }
  };
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
        <SettingsToggleRow id="allow_everything_all_chats" label="Allow everything in every chat"
          description="No chat or agent asks for permission: commands, sandbox, web, file changes and tools all run. Questions still come to you."
          checked={allowAll} disabled={!accessLoaded || savingAllowAll}
          onChange={(checked) => { void saveAllowAll(checked); }} />
        <p role="status">{allowAllStatus}</p>
        <p>Otherwise tool approvals are requested in the owning chat. Allow once applies to that request; saved approvals apply to that chat. Strict file protection still takes precedence.</p>
        <SavedPermissionsSection />
      </section>
      <hr className="general-tab-divider" />
      <section className="general-tab-section">
        <GeneralSectionHeader icon={<Icons.Settings />} title="Anonymous usage stats" description="Counts which features you use, how long Ducky is open and which errors happen, by code. A random install id, never your chats, files, project names or email. Off empties the queue at once." />
        <SettingsToggleRow id="usage_stats" label="Send anonymous usage stats" description="Helps fix what breaks most. Signed in, the counts are linked to your account so support can see your sessions."
          checked={usageStats} disabled={!usageLoaded || savingUsage}
          onChange={(checked) => { void saveUsage(checked); }} />
        <p role="status">{usageStatus}</p>
      </section>
    </div>
  );
}
