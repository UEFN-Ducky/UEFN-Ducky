import { useCallback, useEffect, useState } from "react";
import { Icons } from "../../icons/Icons";
import { getApi } from "../../hooks/usePanelApi";
import type { CrashReportDto } from "../../types/panel";
import { GeneralSectionHeader } from "./GeneralSectionHeader";

function when(at: number): string {
  const d = new Date(at * 1000);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** Settings → Support: crash reports kept on this PC; unsent ones can be sent or deleted. */
export function CrashReportsSection() {
  const [reports, setReports] = useState<CrashReportDto[]>([]);
  const [open, setOpen] = useState<string>("");
  const [busy, setBusy] = useState<string>("");
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    void getApi()
      ?.crash_reports?.()
      .then((state) => setReports(Array.isArray(state?.reports) ? state.reports : []));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const send = async (id: string) => {
    setBusy(id);
    setError("");
    const out = await getApi()?.crash_report_send?.(id);
    setBusy("");
    if (!out?.ok) setError(String(out?.error || "Couldn't send the report."));
    refresh();
  };

  const remove = async (id: string) => {
    setBusy(id);
    await getApi()?.crash_report_delete?.(id);
    setBusy("");
    if (open === id) setOpen("");
    refresh();
  };

  return (
    <section className="general-tab-section">
      <GeneralSectionHeader
        icon={<Icons.ErrorCircle />}
        title="Crash reports"
        description="If UEFN Ducky closes unexpectedly, it asks you next time whether to send what happened. Reports have the app and Windows version, the startup steps and the error. Never your chats, files, name, email or keys."
      />
      <div className="general-tab-toggle-card crash-reports">
        {reports.length === 0 ? (
          <p className="crash-reports-empty">No crash reports. Ducky hasn't closed unexpectedly.</p>
        ) : (
          reports.map((r) => (
            <div key={r.id} className="crash-report-row">
              <div className="crash-report-head">
                <div className="crash-report-meta">
                  <span className="crash-report-title">{r.message.replace(/^Crash report: /, "")}</span>
                  <span className="crash-report-when">
                    {when(r.at)} · {r.sent ? "Sent" : "Not sent"}
                  </span>
                </div>
                <div className="crash-report-actions">
                  <button type="button" className="settings-btn" onClick={() => setOpen(open === r.id ? "" : r.id)}>
                    {open === r.id ? "Hide" : "View"}
                  </button>
                  {!r.sent ? (
                    <button
                      type="button"
                      className="settings-btn general-tab-btn-primary"
                      disabled={busy === r.id}
                      onClick={() => void send(r.id)}
                    >
                      {busy === r.id ? "Sending…" : "Send"}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="settings-btn"
                    disabled={busy === r.id}
                    onClick={() => void remove(r.id)}
                  >
                    Delete
                  </button>
                </div>
              </div>
              {open === r.id ? (
                <pre className="crash-report-text" tabIndex={0}>
                  {r.text}
                </pre>
              ) : null}
            </div>
          ))
        )}
        {error ? (
          <p className="support-tab-status is-err" role="alert">
            {error}
          </p>
        ) : null}
      </div>
    </section>
  );
}
