/**
 * Which rows of a variable-height list are worth rendering.
 *
 * Rows in the Changes timeline are not all the same height — a run header is
 * taller than a change, and an expanded change grows by a step per entry — so
 * the window is found from a running total rather than by dividing by one
 * fixed row height.
 */

/** Running total of heights: `offsets[i]` is the top of item `i`, last entry is the total. */
export function prefixOffsets(heights: readonly number[]): number[] {
  const acc = new Array<number>(heights.length + 1);
  acc[0] = 0;
  for (let i = 0; i < heights.length; i++) acc[i + 1] = acc[i] + heights[i];
  return acc;
}

/** Index of the last item whose top is at or before `value`. */
function indexAt(offsets: readonly number[], count: number, value: number): number {
  let lo = 0;
  let hi = count;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (offsets[mid + 1] <= value) lo = mid + 1;
    else hi = mid;
  }
  return Math.min(lo, count);
}

export interface VirtualWindow {
  start: number;
  /** Exclusive. */
  end: number;
  padTop: number;
  padBottom: number;
}

export function visibleWindow(
  offsets: readonly number[],
  count: number,
  scrollTop: number,
  viewportHeight: number,
  overscanPx: number,
): VirtualWindow {
  const total = offsets[count] ?? 0;
  if (count === 0) return { start: 0, end: 0, padTop: 0, padBottom: 0 };
  const top = Math.max(0, scrollTop - overscanPx);
  const bottom = scrollTop + viewportHeight + overscanPx;
  const start = indexAt(offsets, count, top);
  let end = start;
  while (end < count && offsets[end] < bottom) end++;
  return {
    start,
    end,
    padTop: offsets[start] ?? 0,
    padBottom: Math.max(0, total - (offsets[end] ?? total)),
  };
}
