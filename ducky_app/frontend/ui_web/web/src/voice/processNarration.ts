/**
 * Short conversational lines for live-voice process chatter
 * ("Running tool to…", "Thinking about…") — not full thought dumps.
 */

import { humanToolLabel } from "../utils/agentActivity";

/** 0 = mute process talk; 1 = tools + thinking snippets. */
export function clampProcessTalk(n: unknown): number {
  const v = typeof n === "number" ? n : Number(n);
  return Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0.7;
}

/** Tool starts — always when processTalk > 0. */
export function shouldNarrateTool(processTalk: number): boolean {
  return clampProcessTalk(processTalk) > 0;
}

/** Thinking — only at mid/high processTalk (tools alone at low). */
export function shouldNarrateThinking(processTalk: number): boolean {
  return clampProcessTalk(processTalk) >= 0.4;
}

/** Tool results — same threshold as thinking. */
export function shouldNarrateToolResult(processTalk: number): boolean {
  return clampProcessTalk(processTalk) >= 0.4;
}

/** Include a short topic snippet (vs bare "Thinking.") at high processTalk. */
export function shouldNarrateThinkingDetail(processTalk: number): boolean {
  return clampProcessTalk(processTalk) >= 0.7;
}

/** "Running tool to reading file." — short, spoken status. */
export function speakableToolLine(toolName: string): string {
  const label = humanToolLabel((toolName || "").trim() || "something").toLowerCase();
  return `Running tool to ${label}.`;
}

/** First ~N words, no newlines — for "Thinking about …". */
export function thinkingTopic(text: string, maxWords = 8): string {
  const clean = (text || "")
    .replace(/[`*_#>[\](){}]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!clean) return "";
  const words = clean.split(" ").filter(Boolean).slice(0, maxWords);
  let out = words.join(" ");
  if (out.length > 48) out = `${out.slice(0, 47).trim()}…`;
  return out;
}

export function speakableThinkingLine(text: string, processTalk: number): string | null {
  if (!shouldNarrateThinking(processTalk)) return null;
  if (!shouldNarrateThinkingDetail(processTalk)) return "Thinking.";
  const topic = thinkingTopic(text);
  return topic ? `Thinking about ${topic}.` : "Thinking.";
}
