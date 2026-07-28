/**
 * Shared path: load last assistant message → conversational spoken summary → TTS.
 */

import { runBridgeJob } from "../hooks/bridgeJobAsync";
import { getApi } from "../hooks/usePanelApi";
import { ttsEngine } from "./ttsEngine";
import { getVoiceSettings, resolveSpeed, resolveVoiceId } from "./voiceSettings";

export type SpeakReplyResult = {
  ok: boolean;
  spoken?: string;
  error?: string;
};

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** Extract speakable text from a load_messages row (API uses ``text``, not ``content``). */
export function assistantTextFromRow(row: {
  role?: string;
  text?: string;
  content?: string;
  incomplete?: boolean;
  error?: string;
}): string {
  if (row?.role !== "assistant") return "";
  const text = String(row.text ?? row.content ?? "").trim();
  if (text) return text;
  return "";
}

/**
 * Fetch the latest non-empty assistant message text for a conversation.
 * Retries briefly — assistant_done can fire before the turn is fully persisted.
 */
export async function loadLastAssistantText(convId: string, attempts = 6): Promise<string> {
  const api = getApi();
  if (!api?.load_messages) return "";

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (attempt > 0) await sleep(120 * attempt);
    const rows = await api.load_messages(convId);
    for (let i = rows.length - 1; i >= 0; i -= 1) {
      const row = rows[i] as {
        role?: string;
        text?: string;
        content?: string;
        incomplete?: boolean;
      };
      // Skip incomplete/empty interrupted turns (e.g. bad model) — keep looking.
      if (row?.incomplete && !assistantTextFromRow(row)) continue;
      const text = assistantTextFromRow(row);
      if (text) return text;
    }
  }
  return "";
}

/** Summarize assistant text for speech (shared by speak + enqueue paths). */
export async function prepareSpokenText(text: string): Promise<string> {
  const cleaned = (text || "").trim();
  if (!cleaned) return "";
  const settings = getVoiceSettings();
  const looksCodey = /```|^\s*(def |class |function |import |from |const |let |#include)/m.test(cleaned);
  if (cleaned.length <= 200 && !looksCodey) return cleaned;
  const model = settings.summaryModel || "";
  const result = await runBridgeJob<{
    ok?: boolean;
    text?: string;
    error?: string;
  }>("voice_summarize_reply", [cleaned, model], 90_000);
  if (!result?.ok || !result.text) {
    return cleaned.length > 280 ? `${cleaned.slice(0, 280).trim()}…` : cleaned;
  }
  return String(result.text).trim();
}

/**
 * Summarize (if needed) and speak the reply. Returns the spoken text.
 * Always uses the cheap voice summary model — never reads raw code aloud when summarizing.
 */
export async function speakReply(
  convId: string,
  voiceId?: string,
  opts?: {
    onSpokenText?: (text: string) => void;
    /** Prefer this text over load_messages (group turns / event payload). */
    text?: string;
    /** Queue behind current speech with this speaker label (group roundtable). */
    enqueue?: boolean;
    speaker?: string;
    /** Per-ducky talking speed (0/undefined → global default). */
    speed?: number;
  },
): Promise<SpeakReplyResult> {
  try {
    const text = (opts?.text || "").trim() || (await loadLastAssistantText(convId));
    if (!text) return { ok: false, error: "empty reply" };

    const settings = getVoiceSettings();
    const voice = resolveVoiceId(voiceId) || settings.defaultVoice;
    const rate = resolveSpeed(opts?.speed);
    const spoken = await prepareSpokenText(text);
    if (!spoken) return { ok: false, error: "empty spoken text" };
    opts?.onSpokenText?.(spoken);
    if (opts?.enqueue) {
      ttsEngine.enqueueUtterance(spoken, voice, opts.speaker, rate);
    } else {
      ttsEngine.speak(spoken, voice, rate);
    }
    return { ok: true, spoken };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}
