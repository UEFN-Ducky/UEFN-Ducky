import type { HeaderProblemsAction } from "../contexts/AppHeaderActionsContext";

export type ProblemsDraftPayload = Pick<HeaderProblemsAction, "files" | "errorCount" | "warningCount">;

export function formatProblemsDraft({ files, errorCount, warningCount }: ProblemsDraftPayload): string {
  const summaryParts: string[] = [];
  if (errorCount > 0) summaryParts.push(`${errorCount} error${errorCount === 1 ? "" : "s"}`);
  if (warningCount > 0) summaryParts.push(`${warningCount} warning${warningCount === 1 ? "" : "s"}`);
  const summary = summaryParts.join(", ");

  const lines = [`Fix these Verse problems (${summary}):`, ""];
  for (const file of files) {
    const fileName = file.path.split("/").pop() || file.path;
    lines.push(`**${fileName}** (\`${file.path}\`)`);
    for (const item of file.items) {
      const tag = item.severity === "warning" ? "warning" : "error";
      lines.push(`- Ln ${item.line} (${tag}): ${item.message}`);
    }
    lines.push("");
  }
  return lines.join("\n").trim();
}
