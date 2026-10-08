import { useState } from "react";
import { savePermissionSettings } from "../hooks/permissionSettings";
import { openPanelRoute } from "../navigation/openPanelRoute";

export function FileProtectionRecovery() {
  const [saving, setSaving] = useState(false);
  const [disabled, setDisabled] = useState(false);
  const [status, setStatus] = useState("");
  const disable = async () => {
    setSaving(true);
    setStatus("");
    try {
      await savePermissionSettings({ ai_ignore_strict: false });
      setDisabled(true);
      setStatus("Strict protection is off. You can continue this chat. File input rules remain active.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not change protection.");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="message-bubble-interrupted-actions">
      <button type="button" className="message-bubble-interrupted-continue" disabled={saving || disabled}
        onClick={() => void disable()}>{saving ? "Saving…" : disabled ? "Strict protection off" : "Turn off Strict protection"}</button>
      <button type="button" className="message-bubble-interrupted-continue" onClick={() => openPanelRoute("settings.permissions")}>Permissions and rules</button>
      {status ? <span role="status">{status}</span> : null}
    </div>
  );
}
