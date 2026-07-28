/** VS Code-style Explorer selection: a highlighted set plus a focus (last click) and a shift anchor. */
export type ExplorerSelection = {
  /** Every highlighted path (files and folders). */
  selected: Set<string>;
  /** Last clicked path — drives the paste target and the focus ring. */
  focus: string | null;
  /** Anchor for shift-range selection. */
  anchor: string | null;
};

/** Header badge when more than one row is selected: "2"…"9", then "9+". */
export function formatSelectionBadge(count: number): string | null {
  if (count <= 1) return null;
  return count > 9 ? "9+" : String(count);
}

export function emptySelection(): ExplorerSelection {
  return { selected: new Set(), focus: null, anchor: null };
}

export function selectOnly(path: string): ExplorerSelection {
  return { selected: new Set([path]), focus: path, anchor: path };
}

/** Ctrl/Meta-click: add or remove one path, and move focus/anchor to it. */
export function toggleSelection(sel: ExplorerSelection, path: string): ExplorerSelection {
  const selected = new Set(sel.selected);
  if (selected.has(path)) selected.delete(path);
  else selected.add(path);
  return { selected, focus: path, anchor: path };
}

/** Shift-click: select the contiguous run between the anchor and `path` in visible order. */
export function rangeSelection(
  sel: ExplorerSelection,
  path: string,
  visibleOrder: readonly string[],
): ExplorerSelection {
  const anchor = sel.anchor ?? path;
  const a = visibleOrder.indexOf(anchor);
  const b = visibleOrder.indexOf(path);
  if (a === -1 || b === -1) return selectOnly(path);
  const [lo, hi] = a <= b ? [a, b] : [b, a];
  return { selected: new Set(visibleOrder.slice(lo, hi + 1)), focus: path, anchor };
}

/**
 * Where a paste lands (derived, never stored): the focused folder, else the focused
 * file's parent, else the Content root.
 */
export function pasteTargetPath(
  sel: ExplorerSelection,
  isDir: (path: string) => boolean,
  parentOf: (path: string) => string,
  contentRoot: string,
): string {
  const f = sel.focus;
  if (!f) return contentRoot;
  return isDir(f) ? f : parentOf(f);
}
