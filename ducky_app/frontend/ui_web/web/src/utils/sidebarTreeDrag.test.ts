import { describe, expect, it } from "vitest";
import type { FolderItem } from "../types/panel";
import { dragId, nestDropId, resolveDragOverId } from "./sidebarTree";

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
