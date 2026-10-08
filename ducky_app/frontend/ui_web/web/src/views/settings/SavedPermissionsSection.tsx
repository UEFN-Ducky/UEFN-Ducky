import { useEffect, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import type { SavedAgentPermissionDto } from "../../types/panel";

export function SavedPermissionsSection() {
  const [rules, setRules] = useState<SavedAgentPermissionDto[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => onApiReady((api) => {
    void api.list_saved_agent_permissions().then((rows) => {
      setRules(rows);
      setLoaded(true);
    }).catch((error: unknown) => setStatus(error instanceof Error ? error.message : "Could not load saved permissions."));
  }), []);
  const revoke = async (row: SavedAgentPermissionDto) => {
    setBusy(true);
    setStatus("Saving…");
    try {
      const api = getApi();
      if (!api) throw new Error("Settings unavailable.");
      const result = await api.revoke_saved_agent_permission(row.conv_id, row.rule);
      if (!result.ok) throw new Error("Could not save the permission change.");
      setRules((current) => current.filter((item) => item.conv_id !== row.conv_id || item.rule !== row.rule));
      setStatus("Permission removed.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not remove permission.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <h3>Saved chat permissions</h3>
      {loaded && !rules.length ? <p>No saved chat permissions.</p> : null}
      {rules.length ? <table className="settings-permissions-table">
        <thead><tr><th scope="col">Chat</th><th scope="col">Permission</th><th scope="col">Action</th></tr></thead>
        <tbody>{rules.map((row) => <tr key={`${row.conv_id}:${row.rule}`}>
          <td>{row.title}</td><td>{row.label}</td>
          <td><button type="button" className="settings-btn" disabled={busy}
            aria-label={`Remove ${row.label} from ${row.title}`} onClick={() => void revoke(row)}>Remove</button></td>
        </tr>)}</tbody>
      </table> : null}
      <p role="status">{status}</p>
    </div>
  );
}
