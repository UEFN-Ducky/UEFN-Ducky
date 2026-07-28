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
