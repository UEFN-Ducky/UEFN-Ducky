import type { EditorLayoutNode } from "../types/panel";

type SplitNode = Extract<EditorLayoutNode, { type: "split" }>;

export function splitNodeById(node: EditorLayoutNode, splitId: string): SplitNode | null {
  if (node.type === "group") return null;
  if (node.id === splitId) return node;
  for (const child of node.children) {
    const found = splitNodeById(child, splitId);
    if (found) return found;
  }
  return null;
}

/** The `--split-pane-flex` of the two panes beside a divider, as the split pane renders it. */
export function liveSplitFlex(root: EditorLayoutNode, splitId: string, childIndex: number): [string, string] | null {
  const node = splitNodeById(root, splitId);
  if (!node || childIndex + 1 >= node.children.length) return null;
  const total = node.sizes.reduce((a, b) => a + b, 0) || node.children.length;
  const flex = (i: number) => `${(node.sizes[i] ?? 1) / total} 1 0%`;
  return [flex(childIndex), flex(childIndex + 1)];
}
