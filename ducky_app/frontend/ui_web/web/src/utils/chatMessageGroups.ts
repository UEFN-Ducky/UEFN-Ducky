import { isStandaloneToolCard } from "../components/tool-cards/toolCategories";
import type { ChatMessage } from "../types/panel";
import { unwrapCodingAgentTool } from "./unwrapCodingAgentTool";

export type ActivityItem =
  | {
      kind: "thinking";
      id: string;
      text: string;
      isStreaming?: boolean;
      incomplete?: boolean;
      author?: ChatMessage["author"];
    }
  | { kind: "tool"; id: string; intent: ChatMessage; result: ChatMessage | null };

export type ChatRow =
  | {
      kind: "bubble";
      id: string;
      role: ChatMessage["role"];
      text: string;
      attachments?: ChatMessage["attachments"];
      isStreaming?: boolean;
      thinking?: string;
      incomplete?: boolean;
      error?: string;
      author?: ChatMessage["author"];
    }
  | { kind: "tool"; id: string; intent: ChatMessage; result: ChatMessage | null }
  /** Consecutive tools + thinking-only bubbles, collapsed like Cursor's activity accordion. */
  | { kind: "activity"; id: string; items: ActivityItem[]; author?: ChatMessage["author"] };

function isThinkingOnlyBubble(row: ChatRow): row is Extract<ChatRow, { kind: "bubble" }> {
  return (
    row.kind === "bubble" &&
    row.role === "assistant" &&
    !!row.thinking?.trim() &&
    !row.text?.trim()
  );
}

function toolNameFromRow(row: Extract<ChatRow, { kind: "tool" }>): string {
  const rawName = row.intent.tool?.name ?? row.result?.tool?.name ?? "";
  const rawArgs = row.intent.tool?.arguments ?? row.result?.tool?.arguments ?? {};
  return unwrapCodingAgentTool(
    rawName,
    rawArgs && typeof rawArgs === "object" && !Array.isArray(rawArgs)
      ? (rawArgs as Record<string, unknown>)
      : {},
  ).name;
}

/** File-edit diffs must never fold into the "N tools" accordion. */
function toolHasFileEdit(row: Extract<ChatRow, { kind: "tool" }>): boolean {
  return Boolean(row.intent.tool?.fileEdit || row.result?.tool?.fileEdit);
}

/** Tools + thinking coalesce into the accordion; writes/diffs/ask stay standalone. */
function isGroupableRow(row: ChatRow): boolean {
  if (isThinkingOnlyBubble(row)) return true;
  if (row.kind !== "tool") return false;
  if (toolHasFileEdit(row)) return false;
  return !isStandaloneToolCard(toolNameFromRow(row));
}

function activityAuthorOf(item: ActivityItem): ChatMessage["author"] | undefined {
  if (item.kind === "tool") return item.intent.author ?? item.result?.author;
  return item.author;
}

function activityAuthorKey(item: ActivityItem | undefined): string {
  if (!item) return "";
  return activityAuthorOf(item)?.member_conv_id ?? "";
}

function toActivityItem(row: ChatRow): ActivityItem | null {
  if (row.kind === "tool") {
    return { kind: "tool", id: row.id, intent: row.intent, result: row.result };
  }
  if (isThinkingOnlyBubble(row)) {
    return {
      kind: "thinking",
      id: row.id,
      text: row.thinking!,
      isStreaming: row.isStreaming,
      incomplete: row.incomplete,
      author: row.author,
    };
  }
  return null;
}

function flushActivityBuffer(buffer: ActivityItem[]): ChatRow | null {
  if (buffer.length === 0) return null;
  const toolCount = buffer.reduce((n, i) => n + (i.kind === "tool" ? 1 : 0), 0);
  const thinkCount = buffer.length - toolCount;
  const author = activityAuthorOf(buffer[0]);
  // Lone thinking (no tools) stays a normal bubble — don't wrap "Thinking…" alone.
  if (toolCount === 0 && thinkCount === 1) {
    const th = buffer[0] as Extract<ActivityItem, { kind: "thinking" }>;
    return {
      kind: "bubble",
      id: th.id,
      role: "assistant",
      text: "",
      thinking: th.text,
      isStreaming: th.isStreaming,
      incomplete: th.incomplete,
      author,
    };
  }
  // Even a single tool becomes "1 tool" accordion — never a bare card between chat.
  return { kind: "activity", id: `activity-${buffer[0].id}`, items: buffer, author };
}

/**
 * Collapse consecutive tool rows and thinking-only assistant bubbles into one
 * activity accordion so a long tool/thought ladder doesn't dominate the chat.
 * Consecutive events from different group speakers become separate blocks.
 */
export function coalesceActivityRows(rows: ChatRow[]): ChatRow[] {
  const out: ChatRow[] = [];
  let buffer: ActivityItem[] = [];

  const flush = () => {
    const row = flushActivityBuffer(buffer);
    buffer = [];
    if (!row) return;
    out.push(row);
  };

  for (const row of rows) {
    if (row.kind === "activity") {
      flush();
      out.push(row);
      continue;
    }
    if (isGroupableRow(row)) {
      const item = toActivityItem(row);
      if (!item) continue;
      const last = out[out.length - 1];
      const incomingKey = activityAuthorKey(item);
      if (buffer.length === 0 && last?.kind === "activity") {
        const lastKey = last.author?.member_conv_id ?? activityAuthorKey(last.items[0]);
        if (incomingKey === lastKey) {
          // Clone — never mutate a memoized prior activity row in place.
          out[out.length - 1] = { ...last, items: [...last.items, item] };
          continue;
        }
        buffer.push(item);
        continue;
      }
      if (buffer.length > 0 && incomingKey !== activityAuthorKey(buffer[0])) {
        flush();
      }
      buffer.push(item);
      continue;
    }
    flush();
    out.push(row);
  }
  flush();
  return out;
}

export function groupChatMessages(messages: ChatMessage[]): ChatRow[] {
  const grouped: ChatRow[] = [];

  // Backend numeric ids and optimistic "opt-N" ids can occasionally collide (e.g.
  // an optimistic tail minted around a server disconnect). Duplicate row ids become
  // duplicate React/virtuoso keys, which makes the list re-render the same item
  // forever while scrolling and crashes. Disambiguate here so every row id is
  // unique: the first occurrence keeps the raw id (stable keys), later ones get a
  // deterministic "#N" suffix based on their order.
  const seen = new Map<string, number>();
  const uniqueId = (base: string): string => {
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    return n === 0 ? base : `${base}#${n}`;
  };

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (msg.role === "tool") {
      const nextMsg = messages[i + 1];
      if (nextMsg && (nextMsg.role === "success" || nextMsg.role === "error")) {
        grouped.push({
          kind: "tool",
          id: uniqueId(String(msg.id)),
          intent: msg,
          result: nextMsg,
        });
        i++;
      } else {
        grouped.push({
          kind: "tool",
          id: uniqueId(String(msg.id)),
          intent: msg,
          result: null,
        });
      }
    } else if (msg.role === "error") {
      // Standalone LLM/agent crash (not a tool result). Previously dropped here,
      // so timeouts showed as empty "Done" with no message.
      grouped.push({
        kind: "bubble",
        id: uniqueId(String(msg.id)),
        role: "assistant",
        text: "",
        incomplete: true,
        error: msg.text || msg.error || "Error",
      });
    } else if (msg.role !== "success") {
      grouped.push({
        kind: "bubble",
        id: uniqueId(String(msg.id)),
        role: msg.role,
        text: msg.text,
        attachments: msg.attachments,
        thinking: msg.thinking,
        incomplete: msg.incomplete,
        error: msg.error,
        author: msg.author,
      });
    }
  }
  return grouped;
}

export function appendStreamRow(
  rows: ChatRow[],
  streamBuffer: string,
  isStreaming = false,
  streamThinking = "",
): ChatRow[] {
  // Show the live bubble when there's answer text OR reasoning still streaming,
  // so the "Thinking…" section appears even before any visible answer arrives.
  if (!streamBuffer && !streamThinking) return rows;
  return [
    ...rows,
    {
      kind: "bubble",
      id: "stream",
      role: "assistant",
      text: streamBuffer,
      isStreaming: isStreaming || undefined,
      thinking: streamThinking || undefined,
    },
  ];
}

/**
 * Group committed + live-turn messages once. Callers should memoize this against
 * message arrays only — not against streamBuffer/streamThinking — so text deltas
 * do not re-walk the full history every frame.
 */
export function buildCommittedChatRows(
  committed: ChatMessage[],
  turnMessages: ChatMessage[],
  inFlight: boolean,
): ChatRow[] {
  const combined = inFlight ? [...committed, ...turnMessages] : committed;
  return coalesceActivityRows(groupChatMessages(combined));
}

/** Build virtuoso rows: committed history + live turn tools/stream, including live reasoning. */
export function buildChatRows(input: {
  committed: ChatMessage[];
  turnMessages: ChatMessage[];
  streamBuffer: string;
  streamThinking?: string;
  inFlight: boolean;
}): ChatRow[] {
  const { committed, turnMessages, streamBuffer, streamThinking = "", inFlight } = input;
  const rows = buildCommittedChatRows(committed, turnMessages, inFlight);
  if (inFlight) {
    return coalesceActivityRows(appendStreamRow(rows, streamBuffer, true, streamThinking));
  }
  return rows;
}

/** Virtuoso row indices for user queries — turn snap anchors. */
export function userTurnRowIndices(rows: ChatRow[]): number[] {
  const indices: number[] = [];
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    if (row.kind === "bubble" && row.role === "user") indices.push(i);
  }
  return indices;
}

/**
 * One user question + the full AI reply that follows it (tools, thoughts,
 * assistant bubbles) until the next user message.
 */
export type ChatTurn = {
  id: string;
  query: ChatRow | null;
  responses: ChatRow[];
};

/** Group flat rows into turn divs: query + response wrap. */
export function groupChatRowsIntoTurns(rows: ChatRow[]): ChatTurn[] {
  const turns: ChatTurn[] = [];
  let current: ChatTurn | null = null;

  const startTurn = (query: ChatRow | null): ChatTurn => {
    const turn: ChatTurn = {
      id: query ? `turn-${query.id}` : `turn-lead-${turns.length}`,
      query,
      responses: [],
    };
    turns.push(turn);
    return turn;
  };

  for (const row of rows) {
    const isUser = row.kind === "bubble" && row.role === "user";
    if (isUser) {
      current = startTurn(row);
      continue;
    }
    if (!current) current = startTurn(null);
    current.responses.push(row);
  }

  return turns;
}
