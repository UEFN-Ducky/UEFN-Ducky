import { Icons } from "../../icons/Icons";
import { useQuickOpenBridge } from "../../contexts/QuickOpenBridge";

interface QuickOpenBarProps {
  className?: string;
}

/** Header command/search field — opens the unified palette (Ctrl+P). */
export function QuickOpenBar({ className }: QuickOpenBarProps) {
  const { openPalette } = useQuickOpenBridge();

  return (
    <button
      type="button"
      className={`quick-open-bar quick-open-bar--field no-drag${className ? ` ${className}` : ""}`}
      onClick={() => openPalette("file")}
      title="Search files, duckies, history (Ctrl+P)"
      aria-label="Search files, duckies, and history"
    >
      <Icons.Search />
      <span className="quick-open-bar-placeholder">Search files, duckies, history…</span>
      <kbd className="quick-open-bar-kbd">Ctrl+P</kbd>
    </button>
  );
}
