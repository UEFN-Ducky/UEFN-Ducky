import { describe, expect, it } from "vitest";
import {
  buildTreeModel,
  childIds,
  indicatorFor,
  isSelfOrDescendant,
  moveNodes,
  originalPlaces,
  resolveDropTarget,
  restorePlaces,
  topLevelSources,
  TREE_ROOT,
  zoneForPointer,
  type DropPolicy,
  type TreeEntry,
} from "./treeMove";

/*
 * root
 * ├─ a (leaf)
 * ├─ b (leaf)
 * ├─ F (branch)
 * │   ├─ f1 (leaf)
 * │   └─ G (branch)
 * │       └─ g1 (leaf)
 * └─ H (branch, empty)
 */
const entries: TreeEntry[] = [
  { id: "a", parentId: TREE_ROOT, branch: false },
  { id: "b", parentId: TREE_ROOT, branch: false },
  { id: "F", parentId: TREE_ROOT, branch: true },
  { id: "f1", parentId: "F", branch: false },
  { id: "G", parentId: "F", branch: true },
  { id: "g1", parentId: "G", branch: false },
  { id: "H", parentId: TREE_ROOT, branch: true },
];
const model = buildTreeModel(entries);
const ordered: DropPolicy = { ordered: true, isExpanded: () => true };
const duckies: DropPolicy = { ordered: true, leavesFirst: true, isExpanded: () => true };
const sorted: DropPolicy = { ordered: false };

describe("zoneForPointer", () => {
  it("splits a branch row in three and a leaf row in two", () => {
    expect(zoneForPointer(2, 20, true, ordered)).toBe("before");
    expect(zoneForPointer(10, 20, true, ordered)).toBe("into");
    expect(zoneForPointer(18, 20, true, ordered)).toBe("after");
    expect(zoneForPointer(4, 20, false, ordered)).toBe("before");
    expect(zoneForPointer(16, 20, false, ordered)).toBe("after");
  });

  it("only drops into rows of a sorted tree, and into project rows", () => {
    expect(zoneForPointer(2, 20, true, sorted)).toBe("into");
    expect(zoneForPointer(2, 20, true, ordered, true)).toBe("into");
  });
});

describe("dropping between rows", () => {
  it("lands exactly between two rows", () => {
    const target = resolveDropTarget(model, ["b"], "a", "before", ordered)!;
    expect(target).toEqual({ parentId: TREE_ROOT, index: 0, into: false });
    const next = moveNodes(model, ["b"], target);
    expect(childIds(next, TREE_ROOT)).toEqual(["b", "a", "F", "H"]);
    expect(indicatorFor(model, ["b"], target, ordered)).toEqual({ kind: "line", anchorId: "a", edge: "before" });
  });

  it("after the last row draws the line under it", () => {
    const target = resolveDropTarget(model, ["a"], "H", "after", { ...ordered, isExpanded: () => false })!;
    expect(target.parentId).toBe(TREE_ROOT);
    expect(indicatorFor(model, ["a"], target, ordered)).toEqual({ kind: "line", anchorId: "H", edge: "after" });
    expect(childIds(moveNodes(model, ["a"], target), TREE_ROOT)).toEqual(["b", "F", "H", "a"]);
  });

  it("below an open folder row is the slot above its first child", () => {
    const target = resolveDropTarget(model, ["a"], "F", "after", ordered)!;
    expect(target).toEqual({ parentId: "F", index: 0, into: false });
    expect(indicatorFor(model, ["a"], target, ordered)).toEqual({ kind: "line", anchorId: "f1", edge: "before" });
  });

  it("keeps chats above folders: a leaf never lands among branches", () => {
    const target = resolveDropTarget(model, ["a"], "H", "before", duckies)!;
    expect(target.index).toBe(1); // after b, the last leaf left once a moves
    const next = moveNodes(model, ["a"], target, duckies);
    expect(childIds(next, TREE_ROOT)).toEqual(["b", "a", "F", "H"]);
    expect(indicatorFor(model, ["a"], target, duckies)).toEqual({ kind: "line", anchorId: "F", edge: "before" });
  });

  it("dropping a row on its own place changes nothing", () => {
    expect(resolveDropTarget(model, ["a"], "a", "after", ordered)).toBeNull();
    expect(resolveDropTarget(model, ["a"], "b", "before", ordered)).toBeNull();
  });
});

describe("dropping into a folder or group", () => {
  it("into a branch appends at its end and highlights it", () => {
    const target = resolveDropTarget(model, ["a"], "H", "into", ordered)!;
    expect(target).toEqual({ parentId: "H", index: 0, into: true });
    expect(indicatorFor(model, ["a"], target, ordered)).toEqual({ kind: "into", targetId: "H" });
    expect(childIds(moveNodes(model, ["a"], target), "H")).toEqual(["a"]);
  });

  it("a sorted tree only ever picks the folder (a row means its parent)", () => {
    expect(resolveDropTarget(model, ["a"], "G", "into", sorted)).toEqual({ parentId: "G", index: null, into: true });
    expect(resolveDropTarget(model, ["a"], "f1", "before", sorted)).toEqual({ parentId: "F", index: null, into: true });
    // Already there: nothing to do.
    expect(resolveDropTarget(model, ["f1"], "G", "before", sorted)).toBeNull();
  });

  it("asks the tree whether this folder takes the drop", () => {
    const readOnlyH: DropPolicy = { ...ordered, acceptsDrop: (parentId) => parentId !== "H" };
    expect(resolveDropTarget(model, ["a"], "H", "into", readOnlyH)).toBeNull();
  });

  it("blank space lands at the end of the tree's own root", () => {
    expect(resolveDropTarget(model, ["f1"], null, "into", { ...ordered, blankParent: "H" })).toEqual({
      parentId: "H",
      index: 0,
      into: true,
    });
  });
});

describe("whole subtrees move and the hierarchy never breaks", () => {
  it("a folder moves with everything inside it", () => {
    const target = resolveDropTarget(model, ["F"], "H", "into", ordered)!;
    const next = moveNodes(model, ["F"], target);
    expect(childIds(next, "H")).toEqual(["F"]);
    expect(childIds(next, "F")).toEqual(["f1", "G"]);
    expect(childIds(next, "G")).toEqual(["g1"]);
    expect(isSelfOrDescendant(next, "H", "g1")).toBe(true);
  });

  it("never into itself or anything below it", () => {
    expect(resolveDropTarget(model, ["F"], "F", "into", ordered)).toBeNull();
    expect(resolveDropTarget(model, ["F"], "G", "into", ordered)).toBeNull();
    expect(resolveDropTarget(model, ["F"], "g1", "before", ordered)).toBeNull();
    expect(resolveDropTarget(model, ["F"], "g1", "before", sorted)).toBeNull();
    // Even when asked directly, the model stays a tree.
    const next = moveNodes(model, ["F"], { parentId: "G", index: 0, into: true });
    expect(next).toBe(model);
  });

  it("a folder and something inside it move once, as the folder", () => {
    expect(topLevelSources(model, ["g1", "F", "missing", "F"])).toEqual(["F"]);
    expect(topLevelSources(model, ["H", "b", "a"])).toEqual(["a", "b", "H"]);
  });

  it("several rows land together in display order", () => {
    const target = resolveDropTarget(model, ["b", "a"], "H", "into", ordered)!;
    const next = moveNodes(model, topLevelSources(model, ["b", "a"]), target);
    expect(childIds(next, "H")).toEqual(["a", "b"]);
    expect(childIds(next, TREE_ROOT)).toEqual(["F", "H"]);
  });
});

describe("undoing a move", () => {
  it("puts every row back exactly where it was", () => {
    const places = originalPlaces(model, ["b", "f1"]);
    const target = resolveDropTarget(model, ["b", "f1"], "H", "into", ordered)!;
    const moved = moveNodes(model, ["b", "f1"], target);
    expect(childIds(moved, "H")).toEqual(["b", "f1"]);
    const back = restorePlaces(moved, places);
    expect(childIds(back, TREE_ROOT)).toEqual(["a", "b", "F", "H"]);
    expect(childIds(back, "F")).toEqual(["f1", "G"]);
    expect(childIds(back, "H")).toEqual([]);
  });
});
