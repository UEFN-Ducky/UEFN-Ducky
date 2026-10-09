import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Icons } from "../../icons/Icons";
import { getApi } from "../../hooks/usePanelApi";
import { CrashReportsSection } from "./CrashReportsSection";
import { GeneralSectionHeader } from "./GeneralSectionHeader";
import { SettingsToggleRow } from "./SettingsToggleRow";

function entries(n: number, unit: string): string {
  return `${n} ${unit}${n === 1 ? "" : "s"}`;
}

export function SupportTab() {
  const [message, setMessage] = useState("");
  const [email, setEmail] = useState("");
  const [includeErrors, setIncludeErrors] = useState(false);
  const [includeLog, setIncludeLog] = useState(false);
  const [errorCount, setErrorCount] = useState(0);
  const [logCount, setLogCount] = useState(0);
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<"idle" | "ok" | "err">("idle");
  const [errorText, setErrorText] = useState("");

  const refreshCounts = useCallback(() => {
    const api = getApi();
    void api?.get_errors?.().then((rows) => {
      const n = Array.isArray(rows) ? rows.length : 0;
      setErrorCount(n);
      if (n === 0) setIncludeErrors(false);
    });
    void api?.get_log?.().then((rows) => {
      const n = Array.isArray(rows) ? rows.length : 0;
      setLogCount(n);
      if (n === 0) setIncludeLog(false);
    });
  }, []);

  useEffect(() => {
    refreshCounts();
  }, [refreshCounts]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const api = getApi();
    const text = message.trim();
    if (!api?.submit_feedback) {
      setStatus("err");
      setErrorText("Feedback isn't available in this build.");
      return;
    }
    if (!text) {
      setStatus("err");
      setErrorText("Write a short message first.");
      return;
    }
    setSending(true);
    setStatus("idle");
    setErrorText("");
    try {
      const out = await api.submit_feedback({
        message: text,
        email: email.trim(),
        include_errors: includeErrors && errorCount > 0,
        include_log: includeLog && logCount > 0,
      });
      if (out?.ok) {
        setStatus("ok");
        setMessage("");
        setIncludeErrors(false);
        setIncludeLog(false);
      } else {
        setStatus("err");
        setErrorText(String(out?.error || "Couldn't send feedback."));
      }
    } catch (err) {
      setStatus("err");
      setErrorText(err instanceof Error ? err.message : "Couldn't send feedback.");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="general-tab-shell support-tab">
      <h2 className="general-tab-page-title">Support</h2>
      <section className="general-tab-section">
        <GeneralSectionHeader
          icon={<Icons.Chat />}
          title="Send feedback"
          description="A bug, an idea or a note. It goes straight to the UEFN Ducky team on uefnducky.org."
        />
        <form className="general-tab-toggle-card support-tab-form" onSubmit={(e) => void handleSubmit(e)}>
          <div className="support-tab-fields">
            <label className="support-tab-field" htmlFor="support-message">
              <span className="support-tab-field-label">Message</span>
              <textarea
                id="support-message"
                className="settings-textarea"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="What happened, or what should we improve?"
                rows={6}
                required
                maxLength={8000}
              />
            </label>
            <label className="support-tab-field" htmlFor="support-email">
              <span className="support-tab-field-label">Email (optional)</span>
              <input
                id="support-email"
                className="settings-input"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="If you'd like a reply"
                autoComplete="email"
              />
            </label>
          </div>
          <SettingsToggleRow
            id="support-include-errors"
            label="Include error log"
            description={errorCount > 0 ? `${entries(errorCount, "entry")} from the last 24 hours.` : "No errors in the last 24 hours."}
            checked={includeErrors && errorCount > 0}
            disabled={errorCount === 0}
            onChange={setIncludeErrors}
          />
          <SettingsToggleRow
            id="support-include-log"
            label="Include app log"
            description={logCount > 0 ? `${entries(logCount, "line")} from the last 24 hours.` : "Nothing logged in the last 24 hours."}
            checked={includeLog && logCount > 0}
            disabled={logCount === 0}
            onChange={setIncludeLog}
          />
          <div className="support-tab-footer">
            <p className="support-tab-privacy">
              No chats, API keys or personal files. Logs leave out your name, email, folders and keys.
            </p>
            {status === "ok" ? (
              <p className="support-tab-status is-ok" role="status">
                Thanks, we got your feedback.
              </p>
            ) : null}
            {status === "err" ? (
              <p className="support-tab-status is-err" role="alert">
                {errorText}
              </p>
            ) : null}
            <button
              type="submit"
              className="settings-btn general-tab-btn-primary"
              disabled={sending || !message.trim()}
            >
              {sending ? "Sending…" : "Send feedback"}
            </button>
          </div>
        </form>
      </section>
      <CrashReportsSection />
    </div>
  );
}
