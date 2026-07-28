import { useEffect } from "react";
import { fileDiagnosticRegistry } from "../lsp/fileDiagnosticRegistry";

/** Re-render when the diagnostic registry changes (live LSP updates, scans). */
export function useVerseDiagnosticsSync(onChange: () => void): void {
  useEffect(() => fileDiagnosticRegistry.subscribe(onChange), [onChange]);
}
