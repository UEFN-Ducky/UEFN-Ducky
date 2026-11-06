/**
 * Promise broker for ducky_ask_user.
 * Sessions are concurrent per chat (conv_id) and show only in that chat (and the group hubs
 * named in groupIds, oldest first). True orphans (no chat open) share one queue, answered one
 * at a time from "Waiting for you" in the header's background activity list.
 */
import { getApi } from "../hooks/usePanelApi";
import type { MessageAuthorDto } from "../types/panel";
import { getFocusedChatForAsk } from "./focusedChatForAsk";
import {
  parseAskUserQuestions,
  type AskUserQuestion,
  type AskUserResult,
} from "./types";

export type AskUserSession = {
  id: string;
  questions: AskUserQuestion[];
  title: string;
  /** Owning chat; empty → answered from the header activity list. */
  convId: string;
  /** How many other asks wait behind this one (orphans or group queue). */
  queueAhead: number;
  groupIds: string[];
  author?: MessageAuthorDto;
  /** The asking agent's turn ended before the answer: Submit sends it to the chat as a message. */
  late?: boolean;
};

export type AskUserRunOpts = {
  groupIds?: string[];
  author?: MessageAuthorDto;
  /** The ui_rpc request it answers: answered in another window, it closes here. */
  requestId?: string;
};

type Pending = {
  id: string;
  questions: AskUserQuestion[];
  title: string;
  convId: string;
  groupIds: string[];
  author?: MessageAuthorDto;
  requestId?: string;
  late?: boolean;
  resolve: (result: AskUserResult | { error: string }) => void;
};

/** Active sessions keyed by id (includes chat-scoped + current orphan). */
const sessions = new Map<string, Pending>();
/** Orphan asks waiting behind the one currently shown in the header activity list. */
let orphanQueue: Pending[] = [];
let orphanActiveId: string | null = null;
let nextId = 1;
const listeners = new Set<() => void>();

function notify(): void {
  for (const cb of listeners) cb();
}

function orphanQueueAhead(): number {
  return orphanQueue.length;
}

function pumpOrphan(): void {
  if (orphanActiveId || !orphanQueue.length) return;
  const next = orphanQueue.shift();
  if (!next) return;
  orphanActiveId = next.id;
  sessions.set(next.id, next);
  notify();
}

function toSession(p: Pending, queueAhead: number): AskUserSession {
  return {
    id: p.id,
    questions: p.questions,
    title: p.title,
    convId: p.convId,
    queueAhead,
    groupIds: p.groupIds,
    author: p.author,
    late: p.late,
  };
}

export function subscribeAskUser(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/** All live sessions (chat-scoped + the current orphan). */
export function listAskUserSessions(): AskUserSession[] {
  const out: AskUserSession[] = [];
  for (const p of sessions.values()) {
    out.push(toSession(p, p.convId ? 0 : orphanQueueAhead()));
  }
  return out;
}

function matchingGroupSessions(cid: string): Pending[] {
  const out: Pending[] = [];
  for (const p of sessions.values()) {
    if (p.groupIds.includes(cid)) out.push(p);
  }
  return out;
}

/** Active ask for a chat, if any. Exact conv first; else oldest group-hub match. */
export function getAskUserSessionForConv(convId: string): AskUserSession | null {
  const cid = (convId || "").trim();
  if (!cid) return null;
  for (const p of sessions.values()) {
    if (p.convId === cid) {
      return toSession(p, 0);
    }
  }
  const matching = matchingGroupSessions(cid);
  if (!matching.length) return null;
  return toSession(matching[0], matching.length - 1);
}

export function countAskUserSessionsForConv(convId: string): number {
  const cid = (convId || "").trim();
  if (!cid) return 0;
  let n = 0;
  for (const p of sessions.values()) {
    if (p.convId === cid || p.groupIds.includes(cid)) n++;
  }
  return n;
}

/** Current orphan session (no chat), if any. */
export function getAskUserSession(): AskUserSession | null {
  if (!orphanActiveId) return null;
  const p = sessions.get(orphanActiveId);
  if (!p) return null;
  return toSession(p, orphanQueueAhead());
}

export function settleAskUser(
  result: AskUserResult | { error: string },
  sessionId?: string,
): void {
  const id = sessionId || orphanActiveId;
  if (!id) return;
  const current = sessions.get(id);
  if (!current) return;
  if ("ok" in result && !result.cancelled && current.questions.some((q) => {
    const answer = result.answers[q.id];
    return q.required && (!answer || answer.skipped || (!answer.selected.length && !answer.text.trim()));
  })) return;
  sessions.delete(id);
  if (orphanActiveId === id) {
    orphanActiveId = null;
  }
  current.resolve(result);
  if (current.late && "ok" in result && !result.skipped_all) deliverLateAnswer(current, result);
  pumpOrphan();
  notify();
}

/**
 * The composer's Send while this chat has an open question: the typed message answers it
 * (every question gets it as a written "other" answer, plus `free_text`) instead of being
 * steered or queued. Returns false when the chat has no open question: send as usual.
 */
export function answerAskUserWithMessage(convId: string, message: string): boolean {
  const text = message.trim();
  const session = text ? getAskUserSessionForConv(convId) : null;
  if (!session) return false;
  const answers: AskUserResult["answers"] = {};
  for (const q of session.questions) answers[q.id] = { selected: [], text, skipped: false };
  settleAskUser({ ok: true, answers, skipped_all: false, free_text: text }, session.id);
  return true;
}

/**
 * The asking agent's turn ended while its card was open (a coding agent's run can end with
 * the question still waiting): the answer would reach no one, so the card says so and Submit
 * sends it into that chat as a message instead.
 */
export function markAskUserLate(convId: string): void {
  let changed = false;
  for (const p of sessions.values()) {
    if (p.convId && p.convId === convId && !p.late) {
      p.late = true;
      changed = true;
    }
  }
  if (changed) notify();
}

export function lateAnswerText(title: string, questions: AskUserQuestion[], result: AskUserResult): string {
  const lines = [`Answer to your question${title ? ` "${title}"` : ""} (it came after your turn had ended):`];
  for (const q of questions) {
    const answer = result.answers[q.id];
    if (!answer || answer.skipped) continue;
    const labels = answer.selected.map((id) => q.options.find((o) => o.id === id)?.label || id);
    const parts = [...labels, answer.text.trim()].filter(Boolean);
    if (parts.length) lines.push(`- ${q.prompt}\n  ${parts.join("; ")}`);
  }
  return lines.join("\n");
}

function deliverLateAnswer(p: Pending, result: AskUserResult): void {
  if (!p.convId) return;
  void Promise.resolve(getApi()?.answer_late_question?.(p.convId, lateAnswerText(p.title, p.questions, result))).catch(() => {});
}

/** The PC's request ids of every question open here (to drop the ones answered while away). */
export function askUserRequestIds(): string[] {
  const ids = [...sessions.values(), ...orphanQueue].map((p) => p.requestId || "").filter(Boolean);
  return [...new Set(ids)];
}

/** Another window (or the phone) answered this request: close it here. */
export function dropAnsweredAskUser(requestId: string): void {
  if (!requestId) return;
  for (const [id, p] of [...sessions]) {
    if (p.requestId === requestId) settleAskUser({ error: "answered in another window" }, id);
  }
  const before = orphanQueue.length;
  orphanQueue = orphanQueue.filter((p) => {
    if (p.requestId !== requestId) return true;
    p.resolve({ error: "answered in another window" });
    return false;
  });
  if (orphanQueue.length !== before) notify();
}

/**
 * Show ask-user UI. Resolves when the user finishes the batch (or errors).
 * With convId (or focused chat): docked above that chat's composer.
 * Only when no chat is available: queued for the header activity list.
 */
export function runAskUser(
  rawQuestions: unknown,
  title = "",
  convId = "",
  opts?: AskUserRunOpts,
): Promise<AskUserResult | { error: string }> {
  const questions = parseAskUserQuestions(rawQuestions);
  if (!questions.length) {
    return Promise.resolve({ error: "questions must be a non-empty list" });
  }
  const cid = String(convId || "").trim() || getFocusedChatForAsk();
  const groupIds = (opts?.groupIds ?? []).map((id) => String(id).trim()).filter(Boolean);
  return new Promise((resolve) => {
    const pending: Pending = {
      id: `ask-${nextId++}`,
      questions,
      title: String(title || "").trim(),
      convId: cid,
      groupIds,
      author: opts?.author,
      requestId: opts?.requestId,
      resolve,
    };
    if (cid) {
      // Keep every unanswered ask. A concurrent request cannot cancel an earlier question.
      sessions.set(pending.id, pending);
      notify();
      return;
    }
    orphanQueue.push(pending);
    pumpOrphan();
    notify();
  });
}

/** Test helper: clear queue between unit tests. */
export function _resetAskUserForTests(): void {
  for (const p of sessions.values()) {
    p.resolve({ error: "reset" });
  }
  for (const p of orphanQueue) {
    p.resolve({ error: "reset" });
  }
  sessions.clear();
  orphanQueue = [];
  orphanActiveId = null;
  notify();
}
