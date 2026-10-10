import { cssEscape } from "../ui-targets/resolve";
import { TREE_ROOT, type DropIndicator } from "./treeMove";

/** The row a tree item is drawn as (the drag handle and drop target). */
export const TREE_ROW_ATTR = "data-tree-row";
/** The row plus everything nested under it (a folder's whole block). */
export const TREE_NODE_ATTR = "data-tree-node";

export const DROP_BEFORE_CLASS = "tree-drop-before";
export const DROP_AFTER_CLASS = "tree-drop-after";
export const DROP_INTO_CLASS = "tree-drop-into";
export const DROP_INTO_BLOCK_CLASS = "tree-drop-into-block";
export const DROP_ROOT_CLASS = "tree-drop-root";

export function findTreeElement(root: ParentNode, attr: string, id: string): HTMLElement | null {
  return root.querySelector<HTMLElement>(`[${attr}="${cssEscape(id)}"]`);
}

/**
 * Draws the drop feedback with classes only: an insertion line on a node's block edge,
 * or a highlighted branch. Nothing changes size, so the tree never reflows under the
 * pointer while dragging.
 */
export class TreeDropDecor {
  private marked: Array<{ el: Element; cls: string }> = [];
  private key = "";

  clear(): void {
    for (const { el, cls } of this.marked) el.classList.remove(cls);
    this.marked = [];
    this.key = "";
  }

  /** `parentOf` finds where a hidden anchor's line should fall back to. */
  show(root: HTMLElement, indicator: DropIndicator | null, parentOf?: (id: string) => string | undefined): void {
    const key = indicator ? `${indicator.kind}|${indicator.kind === "line" ? `${indicator.anchorId}|${indicator.edge}` : indicator.targetId}` : "";
    if (key === this.key && this.marked.every(({ el, cls }) => el.isConnected && el.classList.contains(cls))) return;
    this.clear();
    this.key = key;
    if (!indicator) return;
    if (indicator.kind === "line") {
      const block = findTreeElement(root, TREE_NODE_ATTR, indicator.anchorId) ?? findTreeElement(root, TREE_ROW_ATTR, indicator.anchorId);
      if (block) {
        this.mark(block, indicator.edge === "before" ? DROP_BEFORE_CLASS : DROP_AFTER_CLASS);
        return;
      }
      this.showInto(root, parentOf?.(indicator.anchorId) ?? TREE_ROOT);
      return;
    }
    this.showInto(root, indicator.targetId);
  }

  private showInto(root: HTMLElement, targetId: string): void {
    const row = targetId ? findTreeElement(root, TREE_ROW_ATTR, targetId) : null;
    if (row) {
      this.mark(row, DROP_INTO_CLASS);
      const block = findTreeElement(root, TREE_NODE_ATTR, targetId);
      if (block) this.mark(block, DROP_INTO_BLOCK_CLASS);
      return;
    }
    this.mark(root, DROP_ROOT_CLASS);
  }

  private mark(el: Element, cls: string): void {
    el.classList.add(cls);
    this.marked.push({ el, cls });
  }
}
