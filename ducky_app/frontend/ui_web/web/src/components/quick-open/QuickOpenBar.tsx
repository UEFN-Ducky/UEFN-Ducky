import { Icons } from "../../icons/Icons";
import { useQuickOpenBridge } from "../../contexts/QuickOpenBridge";

interface QuickOpenBarProps {
  className?: string;
}

/** Header search icon — opens the unified palette (Ctrl+P). */
export function QuickOpenBar({ className }: QuickOpenBarProps) {
  const { openPalette } = useQuickOpenBridge();

  return (
    <button
      type="button"
      className={`quick-open-bar no-drag${className ? ` ${className}` : ""}`}
      onClick={() => openPalette("file")}
      title="Search files, duckies, history (Ctrl+P)"
      aria-label="Search files, duckies, and history"
    >
      <Icons.Search />
    </button>
  );
}
