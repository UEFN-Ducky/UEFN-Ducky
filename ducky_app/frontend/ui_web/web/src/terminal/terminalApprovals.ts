/**
 * Agent terminal commands waiting for Allow/Deny. Each one is a card in the chat that
 * asked (no chat: an entry in the header's background activity list). Answered in any
 * window, the backend sends terminal_command_decided and the card closes everywhere.
 */
import { onApiReady } from "../hooks/onApiReady";
import { subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { getApi } from "../hooks/usePanelApi";
import type { AgentEvent } from "../types/panel";
import type { PendingTerminalCommand, TerminalApprovalChoice } from "./types";

export type TerminalApproval = PendingTerminalCommand & {
  /** When this window first saw it (ms), for ordering. */
  seenAt: number;
};

const pending = new Map<string, TerminalApproval>();
/** Answered or timed out: a replayed "pending" for it never opens a card again. */
const decided = new Set<string>();
const MAX_DECIDED = 500;
const listeners = new Set<() => void>();
let snapshot: TerminalApproval[] = [];
let stopInstall: (() => void) | null = null;

function notify(): void {
  snapshot = [...pending.values()].sort((a, b) => a.seenAt - b.seenAt);
  for (const cb of listeners) cb();
}

function markDecided(requestId: string): void {
  decided.add(requestId);
  if (decided.size > MAX_DECIDED) {
    const oldest = decided.values().next().value;
    if (oldest !== undefined) decided.delete(oldest);
  }
}

function fromRow(row: Record<string, unknown>): PendingTerminalCommand | null {
  const requestId = String(row.request_id ?? "").trim();
  const command = String(row.command ?? "");
  if (!requestId || !command) return null;
  return {
    request_id: requestId,
    session_id: String(row.session_id ?? ""),
    command,
    shell: row.shell ? String(row.shell) : undefined,
    cwd: row.cwd ? String(row.cwd) : undefined,
    conv_id: String(row.conv_id ?? "").trim(),
    source: row.source ? String(row.source) : undefined,
    rule_label: String(row.rule_label ?? "").trim(),
    local_only: Boolean(row.local_only),
    created_at: typeof row.created_at === "number" ? row.created_at : undefined,
  };
}

function addApproval(item: PendingTerminalCommand): void {
  if (decided.has(item.request_id) || pending.has(item.request_id)) return;
  const seenAt = item.created_at ? item.created_at * 1000 : Date.now();
  pending.set(item.request_id, { ...item, seenAt });
  notify();
}

/** Closes the card here (answered in this window, another one, or timed out). */
export function dropTerminalApproval(requestId: string): void {
  if (!requestId) return;
  markDecided(requestId);
  if (pending.delete(requestId)) notify();
}

function onEvent(event: AgentEvent): void {
  if (event.type === "terminal_command_decided") {
    dropTerminalApproval(String(event.request_id ?? ""));
    return;
  }
  if (event.type !== "terminal_command_pending") return;
  const item = fromRow(event as unknown as Record<string, unknown>);
  if (item) addApproval(item);
}

/** Listen for cards (idempotent). Mounted early by App/FocusView; any subscriber installs it. */
export function installTerminalApprovals(): void {
  if (stopInstall) return;
  const stopEvents = subscribeAgentEvents(onEvent);
  // A reloaded window (or one opened later) shows the cards still waiting.
  const stopReady = onApiReady((api) => {
    void Promise.resolve(api.terminal_pending_commands?.())
      .then((rows) => {
        if (!Array.isArray(rows)) return;
        for (const row of rows) {
          const item = row && typeof row === "object" ? fromRow(row) : null;
          if (item) addApproval(item);
        }
      })
      .catch(() => {});
  });
  stopInstall = () => {
    stopEvents();
    stopReady();
  };
}

export function subscribeTerminalApprovals(cb: () => void): () => void {
  installTerminalApprovals();
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/** Every waiting card, oldest first (stable array until something changes). */
export function listTerminalApprovals(): TerminalApproval[] {
  return snapshot;
}

export function terminalApprovalsForConv(convId: string): TerminalApproval[] {
  const cid = (convId || "").trim();
  if (!cid) return [];
  return snapshot.filter((item) => item.conv_id === cid);
}

/** The chat's options: Always/Everything only for a chat, and never for a local-only push. */
export function terminalApprovalChoices(item: PendingTerminalCommand): TerminalApprovalChoice[] {
  const inChat = Boolean((item.conv_id || "").trim());
  const out: TerminalApprovalChoice[] = ["once"];
  if (inChat && item.rule_label) out.push("always");
  if (inChat && !item.local_only) out.push("all");
  out.push("deny");
  return out;
}

/**
 * Send the answer. The first answer from any window wins; a card someone already
 * answered just closes. Fails (card stays) only when Ducky could not be reached.
 */
export async function answerTerminalApproval(
  requestId: string,
  choice: TerminalApprovalChoice,
): Promise<{ ok: true } | { ok: false; error: string }> {
  const api = getApi();
  if (!api) return { ok: false, error: "Ducky is not connected." };
  let result: Record<string, unknown> | undefined;
  try {
    result =
      choice === "deny"
        ? await api.terminal_reject_command(requestId)
        : await api.terminal_approve_command(requestId, choice);
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
  if (!result) return { ok: false, error: "Could not reach Ducky. Try again." };
  dropTerminalApproval(requestId);
  return { ok: true };
}

/** Test helper. */
export function _resetTerminalApprovalsForTests(): void {
  stopInstall?.();
  stopInstall = null;
  pending.clear();
  decided.clear();
  notify();
}
