import type { MessageAttachmentDto } from "../types/panel";
import { boundedGet, boundedSet } from "../utils/boundedMap";

/**
 * Undo/redo for each chat's composer: its text, caret and attachments.
 *
 * The composer is a contentEditable box whose paste, Shift+Enter, chip deletes,
 * inserted drafts and sends are all written in code, so the browser's own undo loses
 * track; it is also gone when a chat tab closes. This history lives beside the
 * composer cache (memory only, no UI), so it survives closing and reopening the tab
 * or switching chats. Attachments are kept by reference, so images are not copied.
 */

export interface ComposerSnapshot {
  text: string;
  caret: number;
  attachments: MessageAttachmentDto[];
  /** When the step was recorded, to group typing. */
  at: number;
  /** Typing groups with the next keystrokes; any other change is a step of its own. */
  kind: "type" | "edit";
}

interface History {
  past: ComposerSnapshot[];
  present: ComposerSnapshot | null;
  future: ComposerSnapshot[];
}

const MAX_STEPS = 100;
/** Same as the composer cache: a session can open any number of chats. */
const MAX_CHATS = 24;
/** Keystrokes closer together than this are one undo step (until a space or new line). */
const TYPING_GROUP_MS = 1000;

const histories = new Map<string, History>();

function attachmentKey(a: MessageAttachmentDto): string {
  const body = a.data_base64 || a.text || a.media_url || "";
  return `${a.kind}|${a.name}|${a.mime || ""}|${body.length}`;
}

export function sameAttachments(a: MessageAttachmentDto[], b: MessageAttachmentDto[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((item, i) => attachmentKey(item) === attachmentKey(b[i]));
}

/** The text one keystroke added, or "" when the change was not a single typed character. */
function typedChar(before: string, after: string): string | null {
  const grew = after.length - before.length;
  if (Math.abs(grew) !== 1) return null;
  let i = 0;
  while (i < before.length && i < after.length && before[i] === after[i]) i++;
  if (grew === 1) {
    return after.slice(0, i) + after.slice(i + 1) === before ? after[i] : null;
  }
  return before.slice(0, i) + before.slice(i + 1) === after ? "" : null;
}

function historyFor(chatId: string): History {
  let history = boundedGet(histories, chatId);
  if (!history) {
    history = { past: [], present: null, future: [] };
    boundedSet(histories, chatId, history, MAX_CHATS);
  }
  return history;
}

/** Note the composer's current state. Caret-only moves update the current step. */
export function recordComposer(
  chatId: string,
  text: string,
  caret: number,
  attachments: MessageAttachmentDto[],
  now = Date.now(),
): void {
  if (!chatId) return;
  const history = historyFor(chatId);
  const prev = history.present;
  if (prev && prev.text === text && sameAttachments(prev.attachments, attachments)) {
    prev.caret = caret;
    return;
  }
  const typed = prev && sameAttachments(prev.attachments, attachments) ? typedChar(prev.text, text) : null;
  const next: ComposerSnapshot = { text, caret, attachments, at: now, kind: typed === null ? "edit" : "type" };
  history.future = [];
  // A typed space or new line ends the word, so it starts a new step.
  const extendsTyping =
    prev?.kind === "type" && next.kind === "type" && now - prev.at < TYPING_GROUP_MS && !/\s/.test(typed || "");
  if (prev && !extendsTyping) {
    history.past.push(prev);
    if (history.past.length > MAX_STEPS) history.past.shift();
  }
  history.present = next;
}

function restore(snapshot: ComposerSnapshot): ComposerSnapshot {
  // Typing right after an undo or redo is a new step, never merged into the restored one.
  return { ...snapshot, kind: "edit", at: 0 };
}

/** Step back; returns the state to show, or null when there is nothing to undo. */
export function undoComposer(chatId: string): ComposerSnapshot | null {
  const history = boundedGet(histories, chatId);
  if (!history?.present || history.past.length === 0) return null;
  history.future.push(history.present);
  history.present = restore(history.past.pop()!);
  return history.present;
}

/** Step forward again after an undo; null when there is nothing to redo. */
export function redoComposer(chatId: string): ComposerSnapshot | null {
  const history = boundedGet(histories, chatId);
  if (!history?.present || history.future.length === 0) return null;
  history.past.push(history.present);
  history.present = restore(history.future.pop()!);
  return history.present;
}

/** Ctrl/Cmd+Z undoes, Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y redoes; anything else is not ours. */
export function composerHistoryKey(event: { key: string; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey: boolean }):
  "undo" | "redo" | null {
  if (!(event.ctrlKey || event.metaKey) || event.altKey) return null;
  const key = event.key.toLowerCase();
  if (key === "z") return event.shiftKey ? "redo" : "undo";
  if (key === "y" && !event.shiftKey) return "redo";
  return null;
}

export function _resetComposerHistoryForTests(): void {
  histories.clear();
}
