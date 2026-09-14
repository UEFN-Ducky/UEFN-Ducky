import { Icons } from "../../icons/Icons";
import { useQuickOpenBridge } from "../../contexts/QuickOpenBridge";

interface QuickOpenBarProps {
  className?: string;
  variant?: "icon" | "field";
}

/** Header search — opens the unified palette (Ctrl+P). */
export function QuickOpenBar({ className, variant = "icon" }: QuickOpenBarProps) {
  const { openPalette } = useQuickOpenBridge();
  const isField = variant === "field";

  return (
    <button
      type="button"
      className={`quick-open-bar no-drag${isField ? " quick-open-bar--field" : ""}${className ? ` ${className}` : ""}`}
      onClick={() => openPalette("file")}
      title="Search files, duckies, history (Ctrl+P)"
      aria-label="Search files, duckies, and history"
    >
      <Icons.Search />
      {isField ? <span className="quick-open-bar-placeholder">Search</span> : null}
      {isField ? <span className="quick-open-bar-hint">Ctrl+P</span> : null}
    </button>
  );
}
