import { useEffect, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { useUiTarget } from "../../ui-targets/registry";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { SettingsToggleRow } from "./SettingsToggleRow";

export function AiIgnoreSection() {
  const [patterns, setPatterns] = useState("");
  const [strict, setStrict] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("");
  const targetRef = useUiTarget("settings.general.ai_ignore", {
    kind: "settings_field",
    label: "AI ignore list",
    route: "settings.permissions",
  });

  useEffect(() => onApiReady(() => {
    void Promise.resolve(getApi()?.get_settings()).then((s) => {
      if (!s) throw new Error("Settings unavailable");
      setPatterns((s.ai_ignore_patterns ?? []).join("\n"));
      setStrict(s.ai_ignore_strict !== false);
      setLoaded(true);
    }).catch(() => setStatus("Could not load the ignore list."));
  }), []);

  const save = async () => {
    const api = getApi();
    if (!api) return;
    setSaving(true);
    setStatus("");
    try {
      const result = await api.save_agent_settings({
        ai_ignore_patterns: patterns.split(/\r?\n/).map((p) => p.trim()).filter(Boolean),
        ai_ignore_strict: strict,
      });
      if (!result.startsWith("Saved")) throw new Error(result || "Save failed");
      setStatus("Ignore list saved.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not save the ignore list.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="general-tab-section" ref={targetRef}>
      <GeneralSectionHeader icon={<span aria-hidden>×</span>} title="AI ignore list"
        description="Block AI reads, searches and changes to private files. Only you can edit these rules." />
      <p className="general-tab-section-desc">
        Always protected: <code>.env</code> and <code>.env.*</code> in every folder.
        Add one file, folder or pattern per line, such as <code>secrets/</code>,
        <code>*.key</code> or <code>config/private.json</code>. Folder paths are relative
        to each project; absolute paths are also supported. Rules apply across projects.
        Negation with <code>!</code> is refused.
      </p>
      <label htmlFor="ai-ignore-patterns">Additional protected files and folders</label>
      <textarea id="ai-ignore-patterns" className="settings-textarea" rows={6}
        value={patterns} disabled={!loaded || saving}
        placeholder={"secrets/\n*.key\nconfig/private.json"}
        onChange={(event) => { setPatterns(event.target.value); setStatus(""); }} />
      <SettingsToggleRow id="ai-ignore-strict" label="Strict protection"
        description="Block external coding agents, scripts, Git, third-party tools and screen access until they have an audited sandbox. Turning this off protects only Ducky workspace tools and allows bypasses."
        checked={strict} disabled={!loaded || saving} onChange={setStrict} />
      <p className="general-tab-section-desc">
        Protection cannot erase information already sent to an AI or recognize secrets
        pasted under another name. Stop active agents before changing these rules.
      </p>
      <button type="button" className="settings-btn" disabled={!loaded || saving}
        onClick={() => void save()}>{saving ? "Saving…" : "Save ignore list"}</button>
      <p role="status">{status}</p>
    </section>
  );
}
