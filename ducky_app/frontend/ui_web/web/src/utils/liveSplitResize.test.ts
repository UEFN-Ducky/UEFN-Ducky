import { describe, expect, it } from "vitest";
import type { EditorLayoutNode } from "../types/panel";
import { resizeSplitPair } from "./editorLayoutOps";
import { liveSplitFlex, splitNodeById } from "./liveSplitResize";

const group = (id: string): EditorLayoutNode => ({ type: "group", groupId: id });
// Chat | browser over browser | file: the divider between the last two columns is a pair of
// three, so a whole-editor width made it move at two thirds of the pointer.
const root: EditorLayoutNode = {
  type: "split", id: "row", axis: "row", sizes: [1, 1, 1],
  children: [group("chat"), { type: "split", id: "col", axis: "column", sizes: [1, 1], children: [group("b1"), group("b2")] }, group("file")],
};
const layout = { root, groups: {}, focusedGroupId: "chat" };

describe("live split resize", () => {
  it("finds nested splits", () => {
    expect(splitNodeById(root, "col")?.sizes).toEqual([1, 1]);
    expect(splitNodeById(root, "nope")).toBeNull();
  });

  it("moves the divider exactly as far as the pointer when measured against the pair", () => {
    // The pair (columns 2 and 3) is 600 px of a 900 px editor; drag 60 px to the right.
    const pairPx = 600;
    const next = resizeSplitPair(layout, "row", 1, 60 / pairPx, 0);
    const [a, b] = liveSplitFlex(next.root, "row", 1)!;
    const share = (flex: string) => Number(flex.split(" ")[0]);
    // Column 2 was 300 px (1/3 of 900): now 360 px; column 3 now 240 px.
    expect(share(a) * 900).toBeCloseTo(360);
    expect(share(b) * 900).toBeCloseTo(240);
  });
});
