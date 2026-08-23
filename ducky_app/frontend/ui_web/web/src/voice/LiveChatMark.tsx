/** Pulsing live-chat presence: tab/sidebar dot, or a labeled pill. */
export function LiveChatDot({
  className = "",
  title = "Live chat",
}: {
  className?: string;
  title?: string;
}) {
  return <span className={`live-chat-dot ${className}`.trim()} title={title} aria-label={title} />;
}

export function LiveChatPill({ className = "" }: { className?: string }) {
  return (
    <span className={`live-chat-pill ${className}`.trim()} title="Live chat is on">
      <span className="live-chat-dot" aria-hidden="true" />
      Live
    </span>
  );
}
