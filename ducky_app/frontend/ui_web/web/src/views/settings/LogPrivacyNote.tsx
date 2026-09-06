/** Shared privacy line for Settings → General → Log & Errors. */
export function LogPrivacyNote() {
  return (
    <p className="log-errors-privacy">
      We only keep crash, plugin, and app errors so bugs can be fixed. No chats, API keys, or
      personal files. Nothing older than 24 hours is kept.
    </p>
  );
}
