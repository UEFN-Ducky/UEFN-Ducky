import type { ToolCallData } from "../types/panel";

/** Keep an available command exit code visible without confusing it with a turn error. */
export function toolExitCode(tool: ToolCallData): number | undefined {
  if (Number.isInteger(tool.exitCode)) return tool.exitCode;
  const text = tool.result || "";
  try {
    const data = JSON.parse(text);
    const code = data?.exit_code ?? data?.exitCode;
    if (Number.isInteger(code)) return code;
  } catch { /* Some adapters send plain command output. */ }
  const match = text.match(/(?:process exited with code|exit[ _]code)\s*[:=]?\s*(-?\d+)/i);
  return match ? Number(match[1]) : undefined;
}
