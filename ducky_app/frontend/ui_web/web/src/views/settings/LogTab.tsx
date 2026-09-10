import { useCallback, useEffect, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { copyText } from "../../utils/copyText";
import { copySupportDump } from "./copySupportDump";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { LogPrivacyNote } from "./LogPrivacyNote";

function ScrollIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </svg>
  );
}

export function LogTab() {
  const [lines, setLines] = useState<string[]>([]);
  const [copied, setCopied] = useState<"discord" | "log" | null>(null);

  const refresh = useCallback(() => {
    const api = getApi();
    if (!api) return;
    void api.get_log().then((rows) => setLines(Array.isArray(rows) ? rows : []));
  }, []);

  const handleClear = useCallback(async () => {
    const api = getApi();
    if (!api) return;
    setLines([]);
    try {
      await api.clear_log();
    } finally {
      refresh();
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
            icon={<ScrollIcon />}
            title="Log"
            description={
              <>
                Last 24 hours of panel and plugin activity — older lines are deleted automatically.
                Paste <strong>Copy for Discord</strong> in a bug report.{" "}
                <span className="log-errors-stats">
                  {lines.length} {lines.length === 1 ? "entry" : "entries"}
                </span>
                <LogPrivacyNote />
              </>
            }
          />
          <div className="log-errors-actions no-drag">
            <button
              type="button"
              className="settings-btn mcp-plugin-btn"
              onClick={() => {
                void copySupportDump().then((ok) => {
                  if (!ok) return;
                  setCopied("discord");
                  window.setTimeout(() => setCopied(null), 2000);
                });
              }}
            >
              {copied === "discord" ? "Copied" : "Copy for Discord"}
            </button>
            <button
              type="button"
              className="settings-btn mcp-plugin-btn"
              disabled={lines.length === 0}
              onClick={() => {
                void copyText(lines.join("\n")).then((ok) => {
                  if (!ok) return;
                  setCopied("log");
                  window.setTimeout(() => setCopied(null), 2000);
                });
              }}
            >
              {copied === "log" ? "Copied" : "Copy log"}
            </button>
            <button type="button" className="settings-btn mcp-plugin-btn" onClick={() => void handleClear()}>
              Clear
            </button>
          </div>
        </div>

        <div className="log-errors-console-card">
          <pre className="log-errors-console-pre">
            {lines.length ? lines.join("\n") : "No log entries."}
          </pre>
        </div>
      </section>
    </div>
  );
}
