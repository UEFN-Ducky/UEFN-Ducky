import { Icons } from "../../icons/Icons";
import { useQuickOpenBridge } from "../../contexts/QuickOpenBridge";

interface QuickOpenBarProps {
  className?: string;
}

export function QuickOpenBar({ className }: QuickOpenBarProps) {
  const { openPalette } = useQuickOpenBridge();

  return (
    <button
      type="button"
      className={`quick-open-bar no-drag${className ? ` ${className}` : ""}`}
      onClick={() => openPalette("file")}
      title="Go to File (Ctrl+P)"
      aria-label="Go to File"
    >
      <Icons.Search />
      <span className="quick-open-bar-placeholder">Search…</span>
    </button>
  );
}
