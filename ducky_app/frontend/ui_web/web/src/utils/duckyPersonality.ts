/** Mirror backend format_ducky_personality_block for live token estimates. */
export function formatDuckyPersonalityBlock(name: string, personality: string): string {
  const text = (personality || "").trim();
  if (!text) return "";
  const n = (name || "").trim();
  const nameLine = n && n.toLowerCase() !== "new ducky" ? `You are ${n}, a UEFN assistant ducky.\n` : "";
  return (
    `\n## Ducky personality\n` +
    `${nameLine}${text}\n` +
    "Follow this personality in your replies while still obeying UEFN rules and tool policies.\n"
  );
}

/** Rough token estimate (matches backend fallback when tiktoken is unavailable). */
export function estimatePersonalityTokens(text: string): number {
  const raw = text || "";
  if (!raw) return 0;
  return Math.max(1, Math.ceil(raw.length / 3.2));
}
