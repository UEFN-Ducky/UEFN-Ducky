const record = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;

export function readableLabel(value: string): string {
  const labels: Record<string, string> = {
    workspace_compile_verse: "Compile Verse code",
    ducky_plan_update_node: "Update a plan step",
    result: "Tool response", node_id: "Plan step", in_progress: "In progress",
  };
  if (labels[value]) return labels[value];
  const words = value.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Summarize only recorded facts; a tool response is not a world-state snapshot. */
export function recordedResult(value: unknown): { title: string; facts: [string, string][] } | null {
  const outer = record(value);
  if (!outer) return null;
  let result: unknown = outer.result ?? record(outer.after)?.result ?? outer;
  if (typeof result === "string") {
    try { result = JSON.parse(result); } catch { return null; }
  }
  const data = record(result);
  if (!data) return null;
  const compile = record(data.compile);
  if (compile) {
    const errors = typeof compile.numErrors === "number" ? compile.numErrors : null;
    const warnings = typeof compile.numWarnings === "number" ? compile.numWarnings : null;
    const facts: [string, string][] = [];
    if (errors !== null) facts.push(["Errors", String(errors)]);
    if (warnings !== null) facts.push(["Warnings", String(warnings)]);
    const files = record(data.diagnostics)?.files;
    if (Array.isArray(files)) facts.push(["Files in diagnostic report", String(files.length)]);
    return { title: errors !== null && errors > 0 ? "Compilation reported errors" : "Compilation report", facts };
  }
  const plan = record(data.plan);
  if (plan) {
    const facts: [string, string][] = [];
    if (typeof plan.title === "string") facts.push(["Plan", plan.title]);
    const params = record(outer.params) ?? outer;
    if (typeof params.node_id === "string") facts.push(["Plan step", params.node_id]);
    if (typeof params.status === "string") facts.push(["Recorded status", readableLabel(params.status)]);
    return { title: data.ok === false ? "Plan update was not successful" : "Recorded plan response", facts };
  }
  return null;
}
