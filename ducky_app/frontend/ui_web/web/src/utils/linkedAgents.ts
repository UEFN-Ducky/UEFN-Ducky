import type { ChatMessage, LinkedAgent, LinkedAgentStatus } from "../types/panel";

const CHAT_TOOL_NAMES = new Set([
  "ducky_spawn_chat",
  "ducky_send_chat_message",
  "ducky_create_chat",
]);

export function isChatToolName(name: string): boolean {
  return CHAT_TOOL_NAMES.has(name);
}

function parseToolResultPayload(raw: unknown): Record<string, unknown> | null {
  if (raw === undefined || raw === null || raw === "") return null;
  if (typeof raw === "object" && raw !== null && !Array.isArray(raw)) {
    return raw as Record<string, unknown>;
  }
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
    if (typeof parsed === "object" && parsed !== null && "data" in (parsed as object)) {
      const inner = (parsed as { data?: unknown }).data;
      if (typeof inner === "string") return parseToolResultPayload(inner);
      if (typeof inner === "object" && inner !== null) return inner as Record<string, unknown>;
    }
  } catch {
    return null;
  }
  return null;
}

function mapResultStatus(raw: unknown): LinkedAgentStatus {
  const s = String(raw ?? "").toLowerCase();
  if (s === "running" || s === "pending") return "running";
  if (s === "done") return "done";
  if (s === "timeout") return "timeout";
  if (s === "cancelled") return "cancelled";
  if (s === "error") return "error";
  return "done";
}

export function linkedAgentFromToolResult(
  toolName: string,
  resultRaw: unknown,
): LinkedAgent | null {
  if (!isChatToolName(toolName)) return null;
  const payload = parseToolResultPayload(resultRaw);
  if (!payload) return null;
  const childConvId = String(payload.conv_id ?? payload.id ?? "").trim();
  if (!childConvId) return null;
  const title = String(payload.title ?? "Chat").trim() || "Chat";
  const status = mapResultStatus(payload.status);
  return { childConvId, title, status };
}

/** While a chat tool is still running, resolve the linked child from args + live events. */
export function pendingLinkedFromChatTool(
  toolName: string,
  args: Record<string, unknown>,
  liveAgents: LinkedAgent[],
): LinkedAgent | null {
  if (!isChatToolName(toolName)) return null;
  const convId = String(args.conv_id ?? "").trim();
  if (convId) {
    const live = liveAgents.find((a) => a.childConvId === convId);
    if (live) return live;
    return { childConvId: convId, title: "Chat", status: "running" };
  }
  const running = liveAgents.filter((a) => a.status === "running");
  if (running.length > 0) return running[running.length - 1];
  const title = String(args.chat_title ?? args.folder_name ?? "Sub-agent").trim() || "Sub-agent";
  return { childConvId: "", title, status: "running" };
}

export function parseLinkedAgentsFromMessages(messages: ChatMessage[]): LinkedAgent[] {
  const byId = new Map<string, LinkedAgent>();
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (msg.role !== "tool" || !msg.tool?.name) continue;
    const next = messages[i + 1];
    if (!next || (next.role !== "success" && next.role !== "error")) continue;
    const toolName = next.tool?.name ?? msg.tool.name;
    const resultRaw = next.tool?.result ?? msg.tool.result;
    const linked = linkedAgentFromToolResult(toolName, resultRaw);
    if (linked) byId.set(linked.childConvId, linked);
  }
  return Array.from(byId.values());
}

export function mergeLinkedAgents(current: LinkedAgent[], incoming: LinkedAgent[]): LinkedAgent[] {
  const byId = new Map(current.map((a) => [a.childConvId, a]));
  for (const agent of incoming) {
    const prev = byId.get(agent.childConvId);
    if (!prev) {
      byId.set(agent.childConvId, agent);
      continue;
    }
    const terminal = new Set<LinkedAgentStatus>(["done", "error", "timeout", "cancelled"]);
    if (terminal.has(agent.status) || agent.status === "running") {
      byId.set(agent.childConvId, { ...prev, ...agent });
    }
  }
  return Array.from(byId.values());
}
