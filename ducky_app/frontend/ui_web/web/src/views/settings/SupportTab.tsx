import { useCallback, useEffect, useState, type FormEvent, type MouseEvent } from "react";
import { Icons } from "../../icons/Icons";
import { getApi } from "../../hooks/usePanelApi";
import { burstConfettiFromElement } from "../../utils/confettiBurst";

export function SupportTab() {
  const handleOpenPatreon = (event: MouseEvent<HTMLButtonElement>) => {
    burstConfettiFromElement(event.currentTarget);
    const api = getApi();
    if (api && typeof api.open_patreon_page === "function") {
      void api.open_patreon_page();
    }
  };

  return (
    <div className="support-tab">
      <div>
        <h2 className="support-tab-title">Support UEFN Ducky</h2>
        <p className="support-tab-lead">Thank you for using UEFN Ducky — it genuinely means a lot.</p>
      </div>

      <FeedbackCard />

      <div className="support-tab-card">
        <p className="support-tab-body">
          UEFN Ducky is <strong>primarily maintained by one developer</strong> — about 99% of the
          time. Features, fixes, and day-to-day upkeep come from that one person, plus AI that
          helps ship faster.
        </p>
        <p className="support-tab-body">
          If UEFN Ducky saves you time or helps you ship something cool, consider supporting on Patreon.
          Pledges go to keeping it running: the website, AI bills, hosting, and day-to-day upkeep.
        </p>
        <p className="support-tab-body support-tab-body--muted">
          No pressure — sharing the project with a friend or leaving feedback is support too. But if you
          want to chip in, Patreon is the best way to keep this going.
        </p>

        <div className="support-tab-warn" role="note">
          <p className="support-tab-warn-title">No official token — contributors are not founders</p>
          <p className="support-tab-body">
            There is no official UEFN Ducky or DuckyOS cryptocurrency. The GitHub Contributors list
            is a commit list only. Contributors are not founders and are not authorized to claim
            pump.fun / bump.fun fees. We do not endorse, operate, or receive those coins.
            Support the project on Patreon only.
          </p>
        </div>

        <button type="button" className="support-tab-cta" onClick={handleOpenPatreon}>
          <Icons.Patreon />
          <span>Support on Patreon</span>
        </button>
      </div>
    </div>
  );
}

function FeedbackCard() {
  const [message, setMessage] = useState("");
  const [email, setEmail] = useState("");
  const [includeErrors, setIncludeErrors] = useState(false);
  const [errorCount, setErrorCount] = useState(0);
  const [sending, setSending] = useState(false);
  const [status, setStatus] = useState<"idle" | "ok" | "err">("idle");
  const [errorText, setErrorText] = useState("");

  const refreshErrors = useCallback(() => {
    const api = getApi();
    if (!api?.get_errors) return;
    void api.get_errors().then((rows) => {
      const n = Array.isArray(rows) ? rows.length : 0;
      setErrorCount(n);
      if (n === 0) setIncludeErrors(false);
    });
  }, []);

  useEffect(() => {
    refreshErrors();
  }, [refreshErrors]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const api = getApi();
    if (!api?.submit_feedback) {
      setStatus("err");
      setErrorText("Feedback is not available in this build.");
      return;
    }
    const text = message.trim();
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
      });
      if (out?.ok) {
        setStatus("ok");
        setMessage("");
        setIncludeErrors(false);
      } else {
        setStatus("err");
        setErrorText(String(out?.error || "Could not send feedback."));
      }
    } catch (err) {
      setStatus("err");
      setErrorText(err instanceof Error ? err.message : "Could not send feedback.");
    } finally {
      setSending(false);
    }
  };

  const hasErrors = errorCount > 0;

  return (
    <div className="support-tab-card support-tab-card--feedback">
      <h3 className="support-tab-feedback-title">Send feedback</h3>
      <p className="support-tab-body">
        Bug, idea, or just a note — it goes to the same public form on uefnducky.org.
      </p>
      <form className="support-tab-feedback" onSubmit={(e) => void handleSubmit(e)}>
        <label className="support-tab-field">
          <span>Message</span>
          <textarea
            className="support-tab-input support-tab-input--area"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="What happened, or what should we improve?"
            rows={5}
            required
            maxLength={8000}
          />
        </label>
        <label className="support-tab-field">
          <span>Email (optional)</span>
          <input
            className="support-tab-input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="If you want a reply"
            autoComplete="email"
          />
        </label>
        <label className={`support-tab-check${hasErrors ? "" : " is-disabled"}`}>
          <input
            type="checkbox"
            checked={includeErrors && hasErrors}
            disabled={!hasErrors}
            onChange={(e) => setIncludeErrors(e.target.checked)}
          />
          <span>
            {hasErrors
              ? `Include error log (${errorCount} ${errorCount === 1 ? "entry" : "entries"} in the last 24 hours)`
              : "Include error log (none in the last 24 hours)"}
          </span>
        </label>
        <p className="support-tab-body support-tab-body--muted">
          No chats, API keys, or personal files. The error log is the same 24-hour list in Settings
          → Errors.
        </p>
        {status === "ok" ? (
          <p className="support-tab-feedback-status is-ok" role="status">
            Thanks — we got your feedback.
          </p>
        ) : null}
        {status === "err" ? (
          <p className="support-tab-feedback-status is-err" role="alert">
            {errorText}
          </p>
        ) : null}
        <button type="submit" className="support-tab-send" disabled={sending || !message.trim()}>
          {sending ? "Sending…" : "Send feedback"}
        </button>
      </form>
    </div>
  );
}
