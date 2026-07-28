import { useCallback, useEffect, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { GeneralSectionHeader } from "./GeneralSectionHeader";

/** Shape returned by the Python panel API (get_urc_status). Read defensively. */
interface UrcStatus {
  available?: boolean;
  path?: string;
  project_root?: string;
  staged_count?: number;
  unstaged_count?: number;
  staged?: string[];
  unstaged?: string[];
  source?: string;
  error?: string;
}

function BranchIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <line x1="6" y1="3" x2="6" y2="15" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
      <path d="M18 9a9 9 0 01-9 9" />
    </svg>
  );
}

function FileList({ title, files }: { title: string; files: string[] }) {
  if (files.length === 0) return null;
  return (
    <div className="urc-file-group">
      <div className="urc-file-group-title">{title}</div>
      <ul className="urc-file-list">
        {files.map((f) => (
          <li key={f} className="urc-file">
            {f}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function SourceControlTab() {
  const [status, setStatus] = useState<UrcStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [result, setResult] = useState("");

  const refresh = useCallback(async () => {
    const api = getApi();
    if (!api) return;
    setLoading(true);
    try {
      const res = (await api.get_urc_status()) as UrcStatus;
      setStatus(res);
    } catch (e) {
      setStatus({ available: false, error: e instanceof Error ? e.message : String(e) });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleCommit = async () => {
    const api = getApi();
    if (!api) return;
    setBusy(true);
    setResult("");
    try {
      const res = (await api.urc_commit(message)) as { ok?: boolean; message?: string; error?: string };
      setResult(res.error ? `Error: ${res.error}` : res.message || "Check-in triggered.");
      if (res.error === undefined) setMessage("");
      await refresh();
    } catch (e) {
      setResult(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const handlePush = async () => {
    const api = getApi();
    if (!api) return;
    setBusy(true);
    setResult("");
    try {
      const res = (await api.urc_push()) as { ok?: boolean; error?: string };
      setResult(res.error ? res.error : "Push complete.");
      await refresh();
    } catch (e) {
      setResult(`Error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const available = status?.available === true;
  const staged = status?.staged ?? [];
  const unstaged = status?.unstaged ?? [];
  const stagedCount = status?.staged_count ?? staged.length;
  const unstagedCount = status?.unstaged_count ?? unstaged.length;

  return (
    <div className="general-tab-shell">
      <h2 className="general-tab-page-title">Source Control</h2>

      <section className="general-tab-section">
        <GeneralSectionHeader
          icon={<BranchIcon />}
          title="Unreal Revision Control (URC)"
          description="Check the status of your UEFN project and check in changes. Uses Epic's urc.exe from your local Fortnite/UEFN install — nothing is bundled."
        />

        {status === null && loading ? <p className="general-tab-footer-note">Loading URC status…</p> : null}

        {status !== null && !available ? (
          <div className="general-tab-toggle-card urc-card">
            <p>URC is not available for this project.</p>
            <p className="general-tab-footer-note">{status.error || "urc.exe not found."}</p>
            <p>
              Install Fortnite/UEFN, or set the <code className="general-tab-inline-code">URC_CLI_LOCATION</code>{" "}
              environment variable to your <code className="general-tab-inline-code">urc.exe</code> path.
            </p>
          </div>
        ) : null}

        {available ? (
          <>
            <div className="general-tab-toggle-card urc-card">
              <div className="urc-meta">
                Project: <code className="general-tab-inline-code">{status?.project_root || "(none)"}</code>
              </div>
              <div className="urc-meta">
                <strong>{stagedCount}</strong> staged · <strong>{unstagedCount}</strong> unstaged
              </div>
              {stagedCount + unstagedCount === 0 ? (
                <p className="general-tab-footer-note">No pending changes.</p>
              ) : (
                <>
                  <FileList title="Staged" files={staged} />
                  <FileList title="Unstaged" files={unstaged} />
                </>
              )}
            </div>

            <div className="urc-card urc-commit-card">
              <label className="urc-commit-label" htmlFor="urc-commit-message">
                Commit message
              </label>
              <textarea
                id="urc-commit-message"
                className="urc-commit-input"
                rows={2}
                placeholder="Describe your changes…"
                value={message}
                disabled={busy}
                onChange={(e) => setMessage(e.target.value)}
              />
              <div className="general-tab-btn-row">
                <button
                  type="button"
                  className="settings-btn general-tab-btn-primary"
                  disabled={busy}
                  onClick={() => void handleCommit()}
                >
                  Check in
                </button>
                <button type="button" className="settings-btn" disabled={busy} onClick={() => void handlePush()}>
                  Push
                </button>
                <button type="button" className="settings-btn" disabled={loading || busy} onClick={() => void refresh()}>
                  Refresh
                </button>
              </div>
              <p className="general-tab-footer-note">
                Push/pull require the Lore SDK v2 sidecar; the URC CLI path supports status and check-in.
              </p>
            </div>
          </>
        ) : null}

        {!available && status !== null ? (
          <div className="general-tab-btn-row">
            <button type="button" className="settings-btn" disabled={loading} onClick={() => void refresh()}>
              Retry
            </button>
          </div>
        ) : null}

        {result ? <pre className="add-to-uefn-status-pre">{result}</pre> : null}
      </section>
    </div>
  );
}
