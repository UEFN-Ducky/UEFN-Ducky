/**
 * One-line work status per chat ("Reading voice settings… done."), updated on every
 * tool step. The voice lane sends it as context and the live voice view can show it.
 */

import type { ToolCallData } from "../types/panel";
import { shouldSpeakToolStep } from "./processNarration";
import { spokenToolStart } from "./toolNarration";

type ChatWork = {
  line: string;
  /** Tool steps started this turn. */
  steps: number;
  /** Whether each step's start line was spoken (its result follows the same choice). */
  spoken: boolean[];
};

const work = new Map<string, ChatWork>();
const listeners = new Set<() => void>();

function notify() {
  for (const fn of listeners) fn();
}

function entry(chatId: string): ChatWork {
  let row = work.get(chatId);
  if (!row) {
    row = { line: "", steps: 0, spoken: [] };
    work.set(chatId, row);
  }
  return row;
}

function clause(tool: ToolCallData | undefined): string {
  return spokenToolStart(tool).replace(/[.\s]+$/, "");
}

/** A tool started. Returns its spoken line and whether `processTalk` lets it be spoken. */
export function recordToolStart(
  chatId: string,
  tool: ToolCallData | undefined,
  processTalk: number,
): { line: string; speak: boolean } {
  const id = (chatId || "").trim();
  const line = spokenToolStart(tool);
  if (!id) return { line, speak: false };
  const row = entry(id);
  const speak = shouldSpeakToolStep(row.steps, processTalk);
  row.spoken[row.steps] = speak;
  row.steps += 1;
  row.line = `${clause(tool)}…`;
  notify();
  return { line, speak };
}

/** The latest tool finished. Returns whether its start was spoken (so its result may be too). */
export function recordToolDone(chatId: string, tool: ToolCallData | undefined): boolean {
  const id = (chatId || "").trim();
  if (!id) return false;
  const row = entry(id);
  const failed = ["error", "cancelled"].includes((tool?.status || "").toLowerCase());
  row.line = `${clause(tool)}… ${failed ? "failed" : "done"}.`;
  notify();
  return Boolean(row.spoken[row.steps - 1]);
}

/** The turn ended: the next run counts its steps from zero. The last line stays readable. */
export function endWorkTurn(chatId: string): void {
  const row = work.get((chatId || "").trim());
  if (!row) return;
  row.steps = 0;
  row.spoken = [];
}

export function clearWorkStatus(chatId: string): void {
  if (work.delete((chatId || "").trim())) notify();
}

export function getWorkStatus(chatId: string): string {
  return work.get((chatId || "").trim())?.line || "";
}

export function subscribeWorkStatus(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Test helper. */
export function _resetWorkStatus(): void {
  work.clear();
}
