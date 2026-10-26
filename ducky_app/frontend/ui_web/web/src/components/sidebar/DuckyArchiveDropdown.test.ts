import { expect, it } from "vitest";
import { archivedInsideCount, buildArchiveTree } from "./DuckyArchiveDropdown";

const chats = [
  { id: "a", name: "NPC", projectName: "Roguelike" },
  { id: "g", name: "Group1", projectName: "Roguelike", isGroup: true },
  { id: "inner", name: "Inner", projectName: "Roguelike", isGroup: true, parentConvId: "g" },
  { id: "m1", name: "3D Modeler", projectName: "Roguelike", parentConvId: "g" },
  { id: "m2", name: "Verifier", projectName: "Roguelike", parentConvId: "inner" },
  { id: "c", name: "Builder", projectSlug: "other-island" },
  // A ducky whose group was not archived with it stays a row of its own.
  { id: "orphan", name: "Helper", projectName: "Roguelike", parentConvId: "still-active-group" },
];

it("lists archived duckies as one list, each group with what was archived with it", () => {
  const nodes = buildArchiveTree(chats);
  // No project sections: duckies from any project sit together, in order.
  expect(nodes.map((n) => n.chat.id)).toEqual(["a", "g", "c", "orphan"]);
  const group = nodes[1];
  // Duckies first, nested groups after them, like the sidebar.
  expect(group.children.map((n) => n.chat.id)).toEqual(["m1", "inner"]);
  expect(group.children[1].children.map((n) => n.chat.id)).toEqual(["m2"]);
});

it("lists chats in a parent loop or parented to themselves", () => {
  const loop = [
    { id: "x", name: "X", parentConvId: "y" },
    { id: "y", name: "Y", parentConvId: "x" },
    { id: "self", name: "Self", parentConvId: "self" },
  ];
  const nodes = buildArchiveTree(loop);
  const all = (ns: typeof nodes): string[] => ns.flatMap((n) => [n.chat.id, ...all(n.children)]);
  expect(all(nodes).sort()).toEqual(["self", "x", "y"]);
});

it("counts everything a group takes along", () => {
  expect(archivedInsideCount(chats, "g")).toBe(3);
  expect(archivedInsideCount(chats, "inner")).toBe(1);
  expect(archivedInsideCount(chats, "a")).toBe(0);
  expect(archivedInsideCount(chats, "missing")).toBe(0);
});

