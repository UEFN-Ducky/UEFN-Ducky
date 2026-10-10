/**
 * Turns that Ducky or another agent started in this chat are stored as user rows
 * (a2a_broker deliveries, broker notices, team plan notices, team keeper wakes).
 * They must not look like the person typed them, so the chat parses them back into
 * who sent what. Prefixes match group_orchestrator._AGENT_TRAFFIC.
 */

export type AutomatedPartKind = "message" | "notice" | "plan" | "keeper";

export interface AutomatedPart {
  kind: AutomatedPartKind;
  /** Sender name as the backend wrote it, or a fixed label for Ducky's own notices. */
  from: string;
  /** Chat id of the sending (or, for a notice, the silent) agent when known. */
  fromConvId?: string;
  /** Coding agent id (codex, claude_code, cursor) when the sender is one. */
  agent?: string;
  /** Short kind label: Assignment, Answer, Reply expected, Message, Notice, Team plan… */
  tag: string;
  body: string;
}

export interface AutomatedPrompt {
  /** "Reports you have not acted on" when the broker delivered these again. */
  heading?: string;
  parts: AutomatedPart[];
}

const AGENT_LABELS: Record<string, string> = {
  claude_code: "Claude Code",
  codex: "Codex",
  cursor: "Cursor",
};

export function codingAgentName(id: string | undefined): string {
  const clean = (id || "").trim();
  return clean ? AGENT_LABELS[clean] ?? clean : "";
}

/** What Copy puts on the clipboard: the readable message without the protocol lines. */
export function automatedPromptText(prompt: AutomatedPrompt): string {
  if (prompt.parts.length === 1) return prompt.parts[0].body;
  return prompt.parts.map((part) => `${part.from} (${part.tag}):\n${part.body}`.trim()).join("\n\n");
}

const REDELIVERY_HEADING = "Reports you have not acted on";
const MESSAGE_PREFIX = "[ducky:agent-message]";
const NOTICE_PREFIX = "[ducky:agent-notice]";
const PLAN_PREFIX = "[Team plan]";
const KEEPER_PREFIX = "[Ducky keeper]";
const PREFIXES = [MESSAGE_PREFIX, NOTICE_PREFIX, PLAN_PREFIX, KEEPER_PREFIX, REDELIVERY_HEADING];

// a2a_format.sender_label: "Title (chat <id>) [agent]" or "chat <id> [agent]".
const SENDER_RE = /^(?:(.*?) \(chat ([^)\s]+)\)|chat (\S+))(?: \[([\w-]+)\])?/;
const FENCE_LINE_RE = /^(?:\[ducky:untrusted-content [^\]]*\].*|<<<(?:end )?untrusted:[\w-]+>>>)$/;

function parseSender(text: string): { from: string; convId?: string; agent?: string; rest: string } | null {
  const m = SENDER_RE.exec(text);
  if (!m) return null;
  const convId = m[2] || m[3] || "";
  return {
    from: (m[1] || "").trim() || `chat ${convId.slice(0, 8)}`,
    convId: convId || undefined,
    agent: m[4] || undefined,
    rest: text.slice(m[0].length).trim(),
  };
}

function messageTag(protocolLine: string): string | null {
  if (/assignment from your group leader/i.test(protocolLine)) return "Assignment";
  if (/This answers your request/i.test(protocolLine)) return "Answer";
  if (/A reply is expected/i.test(protocolLine)) return "Reply expected";
  return null;
}

function finish(part: AutomatedPart | null, body: string[], out: AutomatedPart[]): void {
  if (!part) return;
  part.body = body.join("\n").trim();
  out.push(part);
}

/** Null for anything a person typed; otherwise the parts in delivery order. */
export function parseAutomatedPrompt(text: string): AutomatedPrompt | null {
  const raw = (text || "").replace(/\r\n/g, "\n").trim();
  if (!raw || !PREFIXES.some((p) => raw.startsWith(p))) return null;

  let rest = raw;
  let heading: string | undefined;
  if (rest.startsWith(REDELIVERY_HEADING)) {
    heading = REDELIVERY_HEADING;
    rest = rest.slice(REDELIVERY_HEADING.length).trim();
  }
  if (rest.startsWith(PLAN_PREFIX)) {
    return { heading, parts: [{ kind: "plan", from: "Team plan", tag: "Plan update", body: rest.slice(PLAN_PREFIX.length).trim() }] };
  }
  if (rest.startsWith(KEEPER_PREFIX)) {
    return { heading, parts: [{ kind: "keeper", from: "Ducky", tag: "Team keeper", body: rest.slice(KEEPER_PREFIX.length).trim() }] };
  }

  const parts: AutomatedPart[] = [];
  let part: AutomatedPart | null = null;
  let body: string[] = [];
  for (const line of rest.split("\n")) {
    const trimmed = line.trim();
    if (trimmed.startsWith(`${MESSAGE_PREFIX} from `)) {
      finish(part, body, parts);
      body = [];
      const sender = parseSender(trimmed.slice(`${MESSAGE_PREFIX} from `.length));
      part = { kind: "message", from: sender?.from || "Another agent", fromConvId: sender?.convId,
        agent: sender?.agent, tag: "Message", body: "" };
      continue;
    }
    if (trimmed.startsWith(MESSAGE_PREFIX) && part?.kind === "message") {
      const tag = messageTag(trimmed);
      // An assignment that also expects a reply stays an assignment.
      if (tag && part.tag !== "Assignment") part.tag = tag;
      continue;
    }
    if (trimmed.startsWith(NOTICE_PREFIX)) {
      const note = trimmed.slice(NOTICE_PREFIX.length).trim();
      if (part?.kind !== "notice") {
        finish(part, body, parts);
        body = [];
        const sender = parseSender(note);
        part = { kind: "notice", from: sender?.from || "Ducky", fromConvId: sender?.convId, agent: sender?.agent,
          tag: "Notice", body: "" };
        body.push((sender ? sender.rest : note).replace(/\s*\(response_id [\w-]+\)\s*$/, ""));
        continue;
      }
      // Tool-call recipes are for the agent, not the person reading the chat.
      if (/^inspect it with:/i.test(note) || /ducky_agent_(?:send|transcript)\(/.test(note)) continue;
      body.push(note);
      continue;
    }
    if (FENCE_LINE_RE.test(trimmed)) continue;
    if (!part) {
      part = { kind: "message", from: "Another agent", tag: "Message", body: "" };
    }
    body.push(line);
  }
  finish(part, body, parts);
  return parts.length ? { heading, parts } : null;
}
