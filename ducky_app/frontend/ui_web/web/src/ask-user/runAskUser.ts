/**
 * Promise broker for ducky_ask_user.
 * Sessions are concurrent per chat (conv_id). True orphans (no chat open) share one modal queue.
 */
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
  /** Owning chat; empty → modal fallback. */
  convId: string;
  /** Orphans only: how many other orphan asks are waiting behind this one. */
  queueAhead: number;
};

type Pending = {
  id: string;
  questions: AskUserQuestion[];
  title: string;
  convId: string;
  resolve: (result: AskUserResult | { error: string }) => void;
};

/** Active sessions keyed by id (includes chat-scoped + current orphan). */
const sessions = new Map<string, Pending>();
/** Orphan asks waiting behind the one currently shown in the modal. */
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

export function subscribeAskUser(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/** All live sessions (chat-scoped + current orphan modal). */
export function listAskUserSessions(): AskUserSession[] {
  const out: AskUserSession[] = [];
  for (const p of sessions.values()) {
    out.push({
      id: p.id,
      questions: p.questions,
      title: p.title,
      convId: p.convId,
      queueAhead: p.convId ? 0 : orphanQueueAhead(),
    });
  }
  return out;
}

/** Active ask for a chat, if any. */
export function getAskUserSessionForConv(convId: string): AskUserSession | null {
  const cid = (convId || "").trim();
  if (!cid) return null;
  for (const p of sessions.values()) {
    if (p.convId === cid) {
      return {
        id: p.id,
        questions: p.questions,
        title: p.title,
        convId: p.convId,
        queueAhead: 0,
      };
    }
  }
  return null;
}

/** Current orphan (modal) session, if any. */
export function getAskUserSession(): AskUserSession | null {
  if (!orphanActiveId) return null;
  const p = sessions.get(orphanActiveId);
  if (!p) return null;
  return {
    id: p.id,
    questions: p.questions,
    title: p.title,
    convId: "",
    queueAhead: orphanQueueAhead(),
  };
}

export function settleAskUser(
  result: AskUserResult | { error: string },
  sessionId?: string,
): void {
  const id = sessionId || orphanActiveId;
  if (!id) return;
  const current = sessions.get(id);
  if (!current) return;
  sessions.delete(id);
  if (orphanActiveId === id) {
    orphanActiveId = null;
  }
  current.resolve(result);
  pumpOrphan();
  notify();
}

/**
 * Show ask-user UI. Resolves when the user finishes the batch (or errors).
 * With convId (or focused chat): docked above that chat's composer.
 * Only when no chat is available: queued in the orphan modal.
 */
export function runAskUser(
  rawQuestions: unknown,
  title = "",
  convId = "",
): Promise<AskUserResult | { error: string }> {
  const questions = parseAskUserQuestions(rawQuestions);
  if (!questions.length) {
    return Promise.resolve({ error: "questions must be a non-empty list" });
  }
  const cid = String(convId || "").trim() || getFocusedChatForAsk();
  return new Promise((resolve) => {
    const pending: Pending = {
      id: `ask-${nextId++}`,
      questions,
      title: String(title || "").trim(),
      convId: cid,
      resolve,
    };
    if (cid) {
      // One open ask per chat — settle any prior if the agent re-asks.
      for (const [id, p] of sessions) {
        if (p.convId === cid) {
          sessions.delete(id);
          p.resolve({ error: "superseded by a newer ask in this chat" });
        }
      }
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
