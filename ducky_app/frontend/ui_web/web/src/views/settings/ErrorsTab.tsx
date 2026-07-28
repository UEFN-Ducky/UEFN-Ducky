import { useCallback, useEffect, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { GeneralSectionHeader } from "./GeneralSectionHeader";

function AlertIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

export function ErrorsTab() {
  const [lines, setLines] = useState<string[]>([]);
  const [pulling, setPulling] = useState(false);

  const refresh = useCallback(() => {
    const api = getApi();
    if (!api) return;
    void api.get_errors().then((rows) => setLines(Array.isArray(rows) ? rows : []));
  }, []);

  const handleClear = useCallback(async () => {
    const api = getApi();
    if (!api) return;
    setLines([]);
    try {
      await api.clear_errors();
    } finally {
      refresh();
    }
  }, [refresh]);

  const handlePullEditorLog = useCallback(async () => {
    const api = getApi();
    if (!api) return;
    setPulling(true);
    try {
      await api.pull_editor_log();
      refresh();
    } finally {
      setPulling(false);
    }
  }, [refresh]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <div className="general-tab-shell log-errors-page-shell">
      <section className="log-errors-section">
        <div className="log-errors-section-head">
          <GeneralSectionHeader
            icon={<AlertIcon />}
            title="Errors"
            description={
              <>
                Panel, MCP, and editor failures.{" "}
                <span className={`log-errors-stats${lines.length > 0 ? " is-warning" : ""}`}>
                  {lines.length} {lines.length === 1 ? "entry" : "entries"}
                </span>
              </>
            }
          />
          <div className="log-errors-actions no-drag">
            <button
              type="button"
              className="settings-btn mcp-plugin-btn"
              disabled={pulling}
              onClick={() => void handlePullEditorLog()}
            >
              {pulling ? "Pulling…" : "Pull editor log"}
            </button>
            <button
              type="button"
              className="settings-btn mcp-plugin-btn"
              disabled={lines.length === 0}
              onClick={() => void navigator.clipboard.writeText(lines.join("\n"))}
            >
              Copy
            </button>
            <button type="button" className="settings-btn mcp-plugin-btn" onClick={() => void handleClear()}>
              Clear
            </button>
          </div>
        </div>

        <div className="log-errors-console-card">
          <pre className="log-errors-console-pre">
            {lines.length ? lines.join("\n") : "No errors recorded."}
          </pre>
        </div>
      </section>
    </div>
  );
}
