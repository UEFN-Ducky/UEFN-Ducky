import { useEffect, useRef, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { useUiTarget } from "../../ui-targets/registry";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { SettingsToggleRow } from "./SettingsToggleRow";
import { PERMISSIONS_CHANGED, savePermissionSettings } from "../../hooks/permissionSettings";

export function AiIgnoreSection() {
  const [patterns, setPatterns] = useState("");
  const [strict, setStrict] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [savingStrict, setSavingStrict] = useState(false);
  const [status, setStatus] = useState("");
  const saveVersion = useRef(0);
  const targetRef = useUiTarget("settings.general.ai_ignore", {
    kind: "settings_field",
    label: "AI ignore list",
    route: "settings.permissions",
  });

  useEffect(() => onApiReady(() => {
    void Promise.resolve(getApi()?.get_settings()).then((s) => {
      if (!s) throw new Error("Settings unavailable");
      setPatterns((s.ai_ignore_patterns ?? []).join("\n"));
      setStrict(s.ai_ignore_strict === true);
      setLoaded(true);
    }).catch(() => setStatus("Could not load the ignore list."));
  }), []);

  useEffect(() => {
    const changed = (event: Event) => {
      const patch = (event as CustomEvent<Record<string, unknown>>).detail;
      if (typeof patch.ai_ignore_strict === "boolean") setStrict(patch.ai_ignore_strict);
    };
    window.addEventListener(PERMISSIONS_CHANGED, changed);
    return () => window.removeEventListener(PERMISSIONS_CHANGED, changed);
  }, []);

  const save = async (patch: Record<string, unknown>) => {
    const version = ++saveVersion.current;
    const changesStrict = "ai_ignore_strict" in patch;
    if (changesStrict) setSavingStrict(true);
    setStatus("Saving…");
    try {
      await savePermissionSettings(patch);
      if (version === saveVersion.current) setStatus("Saved.");
    } catch (error) {
      if (version === saveVersion.current) setStatus(error instanceof Error ? error.message : "Could not save the ignore list.");
      // Keep the switch truthful after a refusal (for example an active agent).
      if ("ai_ignore_strict" in patch) {
        setStrict(patch.ai_ignore_strict !== true);
      }
    } finally {
      if (changesStrict) setSavingStrict(false);
    }
  };

  return (
    <section className="general-tab-section" ref={targetRef}>
      <GeneralSectionHeader icon={<span aria-hidden>×</span>} title="AI ignore list"
        description="Protect files in Ducky's file tools and attachments. Changes save automatically. Only you can edit these rules." />
      <p className="general-tab-section-desc">
        Always protected: <code>.env</code> and <code>.env.*</code> in every folder.
        Add one file, folder or pattern per line, such as <code>secrets/</code>,
        <code>*.key</code> or <code>config/private.json</code>. Folder paths are relative
        to each project; absolute paths are also supported. Rules apply across projects.
        Negation with <code>!</code> is refused.
      </p>
      <label htmlFor="ai-ignore-patterns">Additional protected files and folders</label>
      <textarea id="ai-ignore-patterns" className="settings-textarea" rows={6}
        value={patterns} disabled={!loaded}
        placeholder={"secrets/\n*.key\nconfig/private.json"}
        onChange={(event) => { saveVersion.current++; setPatterns(event.target.value); setStatus(""); }}
        onBlur={() => {
          if (loaded) void save({ ai_ignore_patterns: patterns.split(/\r?\n/).map((p) => p.trim()).filter(Boolean) });
        }} />
      <SettingsToggleRow id="ai-ignore-strict" label="Strict protection"
        description="Optional. Turning this on blocks external coding agents (Codex, Claude Code and Cursor), scripts, Git, third-party tools and screen access until they have an audited sandbox. With this off, file rules protect only Ducky workspace tools; other tools and external agents can bypass them."
        checked={strict} disabled={!loaded || savingStrict} onChange={(checked) => {
          setStrict(checked);
          void save({ ai_ignore_strict: checked });
        }} />
      <p className="general-tab-section-desc">
        Protection cannot erase information already sent to an AI or recognize secrets
        pasted under another name. Stop active agents before editing file rules or
        enabling Strict protection. You can turn Strict protection off immediately.
      </p>
      <p role="status">{status}</p>
    </section>
  );
}
