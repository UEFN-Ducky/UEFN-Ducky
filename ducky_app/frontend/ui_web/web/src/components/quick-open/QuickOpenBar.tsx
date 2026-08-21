import { Icons } from "../../icons/Icons";
import { useQuickOpenBridge } from "../../contexts/QuickOpenBridge";

interface QuickOpenBarProps {
  className?: string;
}

/** Header icon that opens the Go to File palette (Ctrl+P). Compact — never a fake search field. */
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
    </button>
  );
}
