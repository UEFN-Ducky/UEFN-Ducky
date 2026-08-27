/**
 * Map a TTS chunk onto the assistant bubble. Live voice speaks one sentence
 * at a time, so sourceText is rarely the full message.
 */
export function mapReadAlong(
  messageText: string,
  spokenText: string,
  sourceText: string,
  charIndex: number,
): { spokenText: string; charIndex: number } | null {
  const message = messageText || "";
  if (!message.trim()) return null;
  const raw = [spokenText, sourceText].map((s) => (s || "").trim()).filter(Boolean);
  const chunks = raw.concat(raw.map((s) => s.replace(/^[^\n:]{1,40}:\s+/, "")).filter(Boolean));
  for (const chunk of chunks) {
    if (chunk === message || sourceText === message) {
      return { spokenText: spokenText || message, charIndex };
    }
    const at = message.indexOf(chunk);
    if (at < 0) continue;
    let idx = charIndex;
    if (spokenText && !spokenText.startsWith(chunk)) {
      const prefix = spokenText.indexOf(chunk);
      if (prefix >= 0) idx = Math.max(0, charIndex - prefix);
    }
    return { spokenText: message, charIndex: at + idx };
  }
  return null;
}

/**
 * Plain-text karaoke highlight while a message is being spoken.
 */

export function TtsReadAlong({ spokenText, charIndex }: { spokenText: string; charIndex: number }) {
  const spoken = (spokenText || "").trim();
  if (!spoken) return null;
  const i = Math.max(0, Math.min(spoken.length, charIndex));
  let wordEnd = i;
  while (wordEnd < spoken.length && !/\s/.test(spoken[wordEnd]!)) wordEnd += 1;
  return (
    <p className="tts-readalong selectable-text">
      <span className="tts-readalong-spoken">{spoken.slice(0, i)}</span>
      <span className="tts-readalong-current">{spoken.slice(i, wordEnd) || " "}</span>
      <span className="tts-readalong-rest">{spoken.slice(wordEnd)}</span>
    </p>
  );
}
