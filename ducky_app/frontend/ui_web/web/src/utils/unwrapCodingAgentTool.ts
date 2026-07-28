/**
 * Cursor Composer / SDK often wraps MCP calls as CallMcpTool
 * { toolName, args } instead of emitting the real tool name.
 * Ducky's embedded agent uses ducky_call_tool { name, arguments } the same way.
 * Unwrap so tool cards (Read, walkthrough Replay, verse, …) still match.
 */
export function unwrapCodingAgentTool(
  name: string,
  args: Record<string, unknown> | undefined | null,
): { name: string; arguments: Record<string, unknown> } {
  const n = (name || "").trim() || "tool";
  const a =
    args && typeof args === "object" && !Array.isArray(args)
      ? (args as Record<string, unknown>)
      : {};
  const isCursorWrap = n === "CallMcpTool" || n === "call_mcp_tool";
  const isDuckyWrap = n === "ducky_call_tool";
  if (!isCursorWrap && !isDuckyWrap) {
    return { name: n, arguments: a };
  }
  const inner = String(
    isDuckyWrap ? (a.name ?? "") : (a.toolName ?? a.tool_name ?? ""),
  ).trim();
  if (!inner) return { name: n, arguments: a };

  const nested = isDuckyWrap ? a.arguments : (a.args ?? a.arguments);
  let innerArgs: Record<string, unknown> = {};
  if (nested && typeof nested === "object" && !Array.isArray(nested)) {
    innerArgs = nested as Record<string, unknown>;
  } else if (typeof nested === "string" && nested.trim()) {
    try {
      const parsed = JSON.parse(nested) as unknown;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        innerArgs = parsed as Record<string, unknown>;
      }
    } catch {
      /* keep empty */
    }
  }
  return { name: inner, arguments: innerArgs };
}
