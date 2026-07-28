/** Join live-voice transcript segments (manual-send accumulation). */
export function appendLiveUtterance(base: string, next: string): string {
  const a = (base || "").trim();
  const b = (next || "").trim();
  if (!b) return a;
  if (!a) return b;
  return `${a} ${b}`;
}
