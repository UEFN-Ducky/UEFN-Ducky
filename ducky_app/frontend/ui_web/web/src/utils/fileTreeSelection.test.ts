import { describe, expect, it } from "vitest";
import {
  emptySelection,
  formatSelectionBadge,
  pasteTargetPath,
  rangeSelection,
  selectOnly,
  toggleSelection,
} from "./fileTreeSelection";

const order = ["Content/a.verse", "Content/b.verse", "Content/c.verse", "Content/d.verse"];

describe("fileTreeSelection", () => {
  it("formatSelectionBadge shows 2–9 then 9+, and hides for 0–1", () => {
    expect(formatSelectionBadge(0)).toBeNull();
    expect(formatSelectionBadge(1)).toBeNull();
    expect(formatSelectionBadge(2)).toBe("2");
    expect(formatSelectionBadge(9)).toBe("9");
    expect(formatSelectionBadge(10)).toBe("9+");
  });

  it("selectOnly replaces the whole selection", () => {
    const s = selectOnly("Content/b.verse");
    expect([...s.selected]).toEqual(["Content/b.verse"]);
    expect(s.focus).toBe("Content/b.verse");
    expect(s.anchor).toBe("Content/b.verse");
  });

  it("toggle adds then removes, keeping focus on the toggled path", () => {
    let s = selectOnly("Content/a.verse");
    s = toggleSelection(s, "Content/c.verse");
    expect(s.selected).toEqual(new Set(["Content/a.verse", "Content/c.verse"]));
    expect(s.focus).toBe("Content/c.verse");
    s = toggleSelection(s, "Content/c.verse");
    expect(s.selected).toEqual(new Set(["Content/a.verse"]));
  });

  it("range selects the contiguous run from the anchor in visible order (either direction)", () => {
    const s = rangeSelection(selectOnly("Content/b.verse"), "Content/d.verse", order);
    expect([...s.selected]).toEqual(["Content/b.verse", "Content/c.verse", "Content/d.verse"]);
    expect(s.anchor).toBe("Content/b.verse"); // anchor is preserved across shift-clicks
    const up = rangeSelection(s, "Content/a.verse", order);
    expect([...up.selected]).toEqual(["Content/a.verse", "Content/b.verse"]);
  });

  it("range with no anchor / off-list path falls back to selecting only the clicked path", () => {
    expect(rangeSelection(emptySelection(), "Content/c.verse", order).selected).toEqual(
      new Set(["Content/c.verse"]),
    );
    expect(rangeSelection(selectOnly("gone"), "Content/c.verse", order).selected).toEqual(
      new Set(["Content/c.verse"]),
    );
  });

  it("paste target is the focused folder, else the focused file's parent, else Content", () => {
    const dirs = new Set(["Content/Verse"]);
    const isDir = (p: string) => dirs.has(p);
    const parentOf = (p: string) => p.slice(0, p.lastIndexOf("/"));
    expect(pasteTargetPath(selectOnly("Content/Verse"), isDir, parentOf, "Content")).toBe("Content/Verse");
    expect(pasteTargetPath(selectOnly("Content/Verse/x.verse"), isDir, parentOf, "Content")).toBe("Content/Verse");
    expect(pasteTargetPath(emptySelection(), isDir, parentOf, "Content")).toBe("Content");
  });
});
