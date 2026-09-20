import type { AutomationRunDto } from "../types/panel";

export function formatRunLog(run: AutomationRunDto | null, name?: string): string {
  if (!run) return "";
  const lines: string[] = [];
  if (name) lines.push(name);
  if (run.ok === false) lines.push(run.error ? `Failed: ${run.error}` : "Failed");
  else if (run.ok) lines.push("Finished");
  const steps = run.steps || [];
  steps.forEach((s, i) => {
    const label = s.label || s.type || `step ${i + 1}`;
    lines.push(s.ok === false ? `${i + 1}. ${label} — ${s.error || "error"}` : `${i + 1}. ${label} ok`);
  });
  if (!steps.length && run.error && run.ok !== false) lines.push(run.error);
  return lines.join("\n");
}

export function runLogHasContent(run: AutomationRunDto | null): boolean {
  return Boolean(run && ((run.steps && run.steps.length) || run.error || run.ok === false));
}
