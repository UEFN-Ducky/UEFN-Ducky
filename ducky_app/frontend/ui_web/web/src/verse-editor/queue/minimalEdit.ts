/** The one span that differs between two texts, as offsets into `before`.
 *
 * Replacing only this span keeps Monaco's tokens, decorations and undo granularity for
 * every unchanged line; a full-document replace re-tokenized the whole file (~220 ms for
 * 3,000 Verse lines) each time an agent write appeared. */
export interface MinimalEdit {
  start: number;
  end: number;
  text: string;
}

// Never cut between a surrogate pair or inside a CRLF.
const splitsPair = (s: string, i: number): boolean => {
  if (i <= 0 || i >= s.length) return false;
  const prev = s.charCodeAt(i - 1);
  const next = s.charCodeAt(i);
  return (prev >= 0xd800 && prev <= 0xdbff && next >= 0xdc00 && next <= 0xdfff) || (prev === 13 && next === 10);
};

export function minimalEdit(before: string, after: string): MinimalEdit | null {
  if (before === after) return null;
  const max = Math.min(before.length, after.length);
  let start = 0;
  while (start < max && before.charCodeAt(start) === after.charCodeAt(start)) start += 1;
  while (start > 0 && (splitsPair(before, start) || splitsPair(after, start))) start -= 1;
  let tail = 0;
  const maxTail = max - start;
  while (tail < maxTail && before.charCodeAt(before.length - 1 - tail) === after.charCodeAt(after.length - 1 - tail)) {
    tail += 1;
  }
  while (tail > 0 && (splitsPair(before, before.length - tail) || splitsPair(after, after.length - tail))) tail -= 1;
  return { start, end: before.length - tail, text: after.slice(start, after.length - tail) };
}
