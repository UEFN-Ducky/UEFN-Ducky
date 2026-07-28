/**
 * Per-utterance voice match: if preferred voice can't speak the text's script,
 * pick a builtin that can — without changing the saved default voice.
 */

export type SpeechLang = "zh" | "ja" | "ko" | "ru" | "ar" | "en";

/** Dominant non-Latin script, or "en" when mostly Latin / mixed unknown. */
export function detectSpeechLang(text: string): SpeechLang {
  let zh = 0;
  let jaKana = 0;
  let ko = 0;
  let ru = 0;
  let ar = 0;
  let latin = 0;
  for (const ch of text) {
    const c = ch.codePointAt(0) ?? 0;
    if (c >= 0x3040 && c <= 0x30ff) jaKana += 1;
    else if (c >= 0xac00 && c <= 0xd7af) ko += 1;
    else if (c >= 0x4e00 && c <= 0x9fff) zh += 1;
    else if (c >= 0x0400 && c <= 0x04ff) ru += 1;
    else if (c >= 0x0600 && c <= 0x06ff) ar += 1;
    else if ((c >= 0x41 && c <= 0x5a) || (c >= 0x61 && c <= 0x7a)) latin += 1;
  }
  if (jaKana > 0 && jaKana + zh >= ko && jaKana + zh >= ru && jaKana + zh >= ar) return "ja";
  if (ko > 0 && ko >= zh && ko >= ru && ko >= ar) return "ko";
  if (zh > 0 && zh >= ru && zh >= ar && zh >= latin * 0.15) return "zh";
  if (ru > latin && ru >= ar) return "ru";
  if (ar > latin) return "ar";
  return "en";
}

/** BCP-47 primary subtag from a voice id or SpeechSynthesisVoice.lang. */
export function voiceLangTag(voiceIdOrLang: string): string {
  const raw = (voiceIdOrLang || "").trim();
  if (!raw) return "";
  // plugin:piper:en_US-lessac-medium → en_US-lessac-medium
  let id = raw;
  if (id.startsWith("plugin:")) {
    const rest = id.slice("plugin:".length);
    const colon = rest.indexOf(":");
    id = colon > 0 ? rest.slice(colon + 1) : rest;
  } else if (id.startsWith("builtin:")) {
    id = id.slice("builtin:".length);
  }
  const lower = id.toLowerCase();
  // Display names before bare prefix match ("Microsoft…" must not become "mi").
  if (/\bchinese\b|中文|huihui|yaoyao|kangkang/.test(lower)) return "zh";
  if (/\bjapanese\b|日本語|haruka|nanami/.test(lower)) return "ja";
  if (/\bkorean\b|한국어|heami/.test(lower)) return "ko";
  // Piper: en_US-lessac-medium · BCP-47: zh-CN / en-US
  const m = id.match(/^([a-z]{2})[-_][A-Za-z]{2}/i) || id.match(/^([a-z]{2})$/i);
  if (m) return m[1]!.toLowerCase();
  return "";
}

function langsCompatible(voiceLang: string, needed: SpeechLang): boolean {
  if (!voiceLang) return needed === "en";
  return voiceLang === needed;
}

function listSynthVoices(): SpeechSynthesisVoice[] {
  if (typeof speechSynthesis === "undefined") return [];
  return speechSynthesis.getVoices();
}

/** First builtin voice whose lang matches `needed`, or "". */
export function findBuiltinVoiceIdForLang(needed: SpeechLang): string {
  const voices = listSynthVoices();
  const hit =
    voices.find((v) => voiceLangTag(v.lang || "").toLowerCase() === needed) ||
    voices.find((v) => voiceLangTag(v.name || "").toLowerCase() === needed) ||
    null;
  return hit ? `builtin:${hit.name}` : "";
}

/**
 * Keep preferred when it can speak `text`; otherwise swap to a matching builtin
 * for this utterance only. Empty preferred → still try a match when non-English.
 */
export function pickVoiceForText(text: string, preferredVoiceId: string): string {
  const preferred = (preferredVoiceId || "").trim();
  const needed = detectSpeechLang(text || "");
  if (needed === "en") return preferred;

  let prefLang = "";
  if (preferred.startsWith("plugin:")) {
    prefLang = voiceLangTag(preferred);
  } else if (preferred.startsWith("builtin:") || preferred) {
    const name = preferred.startsWith("builtin:") ? preferred.slice("builtin:".length) : preferred;
    const v = listSynthVoices().find((x) => x.name === name || x.voiceURI === name);
    prefLang = voiceLangTag(v?.lang || "") || voiceLangTag(name);
  }

  if (preferred && langsCompatible(prefLang, needed)) return preferred;

  const matched = findBuiltinVoiceIdForLang(needed);
  return matched || preferred;
}
