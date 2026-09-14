import { describe, expect, it } from "vitest";
import type { FolderItem } from "../types/panel";
import { dragId, flattenLayout, nestDropId, resolveDragOverId, unwrapProjectFoldersForLayout, wrapProjectsAsFolders } from "./sidebarTree";

function folder(id: string, name: string, children: FolderItem[] = []): FolderItem {
  return {
    id,
    name,
    parentId: "",
    expanded: true,
    sortOrder: 0,
    chats: [],
    children,
  };
}

describe("resolveDragOverId", () => {
  const roots = [folder("g1", "Roguelike", [folder("f1", "Programming"), folder("f2", "Art")])];
  const rootChats = [{ id: "c1", name: "Producer", sortOrder: 0 }];

  it("chat before/after a folder nests in the parent (not into the folder)", () => {
    const active = dragId("chat", "c1");
    const overFolder = dragId("folder", "f1");
    expect(resolveDragOverId(roots, rootChats, overFolder, "before", active)).toBe(nestDropId("g1"));
    expect(resolveDragOverId(roots, rootChats, overFolder, "after", active)).toBe(nestDropId("g1"));
    expect(resolveDragOverId(roots, rootChats, overFolder, "inside", active)).toBe(nestDropId("f1"));
  });

  it("folder on a chat row targets the first sibling folder slot", () => {
    const active = dragId("folder", "f2");
    const overChat = dragId("chat", "c1");
    expect(resolveDragOverId(roots, rootChats, overChat, "before", active)).toBe(dragId("folder", "g1"));
  });
});

describe("all-projects folder wraps", () => {
  it("wraps current project first and expanded by default", () => {
    const wrapped = wrapProjectsAsFolders(
      [
        { slug: "other", name: "Other", folders: [folder("f1", "Art")], rootChats: [{ id: "c2", name: "B" }] },
        { slug: "here", name: "Here", folders: [folder("f0", "Code")], rootChats: [{ id: "c1", name: "A" }] },
      ],
      "here",
      new Map(),
    );
    expect(wrapped.map((f) => f.id)).toEqual(["project:here", "project:other"]);
    expect(wrapped[0].expanded).toBe(true);
    expect(wrapped[1].expanded).toBe(false);
    expect(wrapped[0].chats[0].projectSlug).toBe("here");
    expect(wrapped[0].children[0].projectSlug).toBe("here");
  });

  it("unwraps only the current project for layout persist", () => {
    const wrapped = wrapProjectsAsFolders(
      [
        { slug: "here", name: "Here", folders: [folder("f0", "Code")], rootChats: [{ id: "c1", name: "A" }] },
        { slug: "other", name: "Other", folders: [folder("f1", "Art")], rootChats: [{ id: "c2", name: "B" }] },
      ],
      "here",
      new Map(),
    );
    const unwrapped = unwrapProjectFoldersForLayout(wrapped, [], "here");
    expect(unwrapped.rootChats.map((c) => c.id)).toEqual(["c1"]);
    expect(unwrapped.folders.map((f) => f.id)).toEqual(["f0"]);
    const patch = flattenLayout(unwrapped.folders, unwrapped.rootChats);
    expect(patch.folders.some((f) => f.id.startsWith("project:"))).toBe(false);
    expect(patch.chats.map((c) => c.id)).toEqual(["c1"]);
  });

  it("flattenLayout skips leaked project: folders", () => {
    const leaked: FolderItem = {
      ...folder("project:other", "Other"),
      chats: [{ id: "stolen", name: "Nope" }],
    };
    const patch = flattenLayout([leaked, folder("f0", "Code")], [{ id: "c1", name: "A" }]);
    expect(patch.folders.map((f) => f.id)).toEqual(["f0"]);
    expect(patch.chats.map((c) => c.id)).toEqual(["c1"]);
  });
});
