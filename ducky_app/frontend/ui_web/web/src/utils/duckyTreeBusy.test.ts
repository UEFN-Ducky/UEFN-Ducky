import { describe, expect, it } from "vitest";
import { anyRunningUnderFolder } from "./duckyTreeBusy";
import type { FolderItem } from "../types/panel";

function chat(id: string): FolderItem["chats"][number] {
  return { id, name: id } as FolderItem["chats"][number];
}

function folder(
  id: string,
  chats: FolderItem["chats"] = [],
  children: FolderItem[] = [],
): FolderItem {
  return {
    id,
    name: id,
    expanded: false,
    chats,
    children,
  } as FolderItem;
}

describe("anyRunningUnderFolder", () => {
  it("finds a running chat in a nested folder", () => {
    const root = folder("root", [], [folder("child", [chat("a"), chat("b")])]);
    const running = new Set(["b"]);
    expect(anyRunningUnderFolder(root, running, new Map())).toBe(true);
  });

  it("finds a running nested subagent via childrenByParent", () => {
    const root = folder("root", [chat("parent")]);
    const childrenByParent = new Map([["parent", [chat("sub")]]]);
    expect(anyRunningUnderFolder(root, new Set(["sub"]), childrenByParent)).toBe(true);
  });

  it("returns false when nothing is running", () => {
    const root = folder("root", [chat("a")], [folder("child", [chat("b")])]);
    expect(anyRunningUnderFolder(root, new Set(), new Map())).toBe(false);
  });
});
