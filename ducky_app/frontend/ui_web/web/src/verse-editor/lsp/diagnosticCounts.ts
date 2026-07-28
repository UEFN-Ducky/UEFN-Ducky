import type { LspDiagnostic } from "./verseLspClient";

export type FileDiagnosticItem = {
  line: number;
  column: number;
  message: string;
  severity: "error" | "warning";
};

function lspSeverityKind(severity?: number): "error" | "warning" | null {
  if (severity === 2) return "warning";
  if (severity === 3 || severity === 4) return null;
  return "error";
}

/** Match applyDiagnostics.ts severity mapping for badge counts. */
export function countDiagnosticSeverities(
  diagnostics: LspDiagnostic[],
): { errors: number; warnings: number } {
  let errors = 0;
  let warnings = 0;
  for (const d of diagnostics) {
    const kind = lspSeverityKind(d.severity);
    if (kind === "warning") warnings += 1;
    else if (kind === "error") errors += 1;
  }
  return { errors, warnings };
}

export function lspDiagnosticsToItems(diagnostics: LspDiagnostic[]): FileDiagnosticItem[] {
  const items: FileDiagnosticItem[] = [];
  for (const d of diagnostics) {
    const kind = lspSeverityKind(d.severity);
    if (!kind) continue;
    items.push({
      line: d.range.start.line + 1,
      column: d.range.start.character + 1,
      message: d.message,
      severity: kind,
    });
  }
  return items;
}
