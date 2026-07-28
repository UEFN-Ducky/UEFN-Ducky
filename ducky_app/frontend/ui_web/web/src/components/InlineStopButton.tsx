/** Compact Cursor-style stop square for sticky query / collapsed live headers. */
export function InlineStopButton({
  onClick,
  className = "inline-stop-btn",
}: {
  onClick: () => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      className={className}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title="Stop"
      aria-label="Stop"
    >
      <span className="inline-stop-btn-icon" aria-hidden />
    </button>
  );
}
