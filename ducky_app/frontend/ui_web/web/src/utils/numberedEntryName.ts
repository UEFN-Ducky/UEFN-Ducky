export interface NumberedEntryNameOptions {
  ext?: string;
}

/**
 * Picks the lowest unused positive integer suffix: Base1, Base2, …
 * Sibling matching is case-insensitive. Optional extension (e.g. ".verse") is appended.
 */
export function numberedEntryName(
  base: string,
  siblings: string[],
  options?: NumberedEntryNameOptions,
): string {
  const ext = options?.ext ?? "";
  const extLower = ext.toLowerCase();
  const used = new Set<number>();

  for (const raw of siblings) {
    const name = raw.trim();
    if (!name) continue;
    let stem = name;
    if (extLower && name.toLowerCase().endsWith(extLower)) {
      stem = name.slice(0, -ext.length);
    }
    const match = new RegExp(`^${escapeRegExp(base)}(\\d+)$`, "i").exec(stem);
    if (match) {
      used.add(Number.parseInt(match[1], 10));
    }
  }

  let n = 1;
  while (used.has(n)) n += 1;
  return `${base}${n}${ext}`;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
