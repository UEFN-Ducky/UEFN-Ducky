import { describe, expect, it } from "vitest";
import type { FolderItem } from "../types/panel";
import {
  duckiesFoldersForDisplay,
  flattenLayout,
  foldersToAutoExpand,
  globalAgentsFolder,
  projectFolderId,
  wrapProjectsAsFolders,
} from "./sidebarTree";
import { buildDuckiesIndex, duckiesLayoutFor, duckiesPlace, projectNodeId } from "./duckiesTreeModel";

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

describe("foldersToAutoExpand", () => {
  it("does not force a project accordion back open", () => {
    expect(foldersToAutoExpand(["project:here", "f0"])).toEqual(["f0"]);
    expect(foldersToAutoExpand(["project:here"])).toEqual([]);
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

  it("saves each project's own layout, never another project's rows", () => {
    const wrapped = wrapProjectsAsFolders(
      [
        { slug: "here", name: "Here", folders: [folder("f0", "Code")], rootChats: [{ id: "c1", name: "A" }] },
        { slug: "other", name: "Other", folders: [folder("f1", "Art")], rootChats: [{ id: "c2", name: "B" }] },
      ],
      "here",
      new Map(),
    );
    const patch = duckiesLayoutFor({ folders: wrapped, rootChats: [] }, "here", "here")!;
    expect(patch.project_slug).toBe("here");
    expect(patch.folders.map((f) => f.id)).toEqual(["f0"]);
    expect(patch.chats.map((c) => c.id)).toEqual(["c1"]);
    const other = duckiesLayoutFor({ folders: wrapped, rootChats: [] }, "other", "here")!;
    expect(other.chats.map((c) => c.id)).toEqual(["c2"]);
    expect(other.folders.map((f) => f.id)).toEqual(["f1"]);
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

  it("keeps the no-island bucket in the data and off the project list", () => {
    const wrapped = wrapProjectsAsFolders(
      [
        { slug: "_no_project", name: "_no_project", folders: [], rootChats: [{ id: "g1", name: "Verse Coder" }] },
        { slug: "here", name: "Roguelike", folders: [], rootChats: [] },
      ],
      "here",
      new Map(),
    );
    expect(wrapped.map((f) => f.id)).toContain(projectFolderId("_no_project"));
    expect(duckiesFoldersForDisplay(wrapped).map((f) => f.name)).toEqual(["Roguelike"]);
    expect(globalAgentsFolder(wrapped)?.chats.map((chat) => chat.id)).toEqual(["g1"]);
    const index = buildDuckiesIndex({ folders: wrapped, rootChats: [] }, "here");
    expect(duckiesPlace(index, projectNodeId("_no_project"))).toEqual({ slug: "_no_project", folderId: "" });
  });

  it("a drop on another project's folder targets that project", () => {
    const wrapped = wrapProjectsAsFolders(
      [
        { slug: "here", name: "Here", folders: [], rootChats: [{ id: "c1", name: "A" }] },
        { slug: "other", name: "Other", folders: [folder("f1", "Art")], rootChats: [] },
      ],
      "here",
      new Map(),
    );
    const index = buildDuckiesIndex({ folders: wrapped, rootChats: [] }, "here");
    expect(duckiesPlace(index, projectNodeId("other"))).toEqual({ slug: "other", folderId: "" });
    expect(duckiesPlace(index, "folder:f1")).toEqual({ slug: "other", folderId: "f1" });
    const patch = duckiesLayoutFor({ folders: wrapped, rootChats: [] }, "here", "here")!;
    expect(patch.chats.map((c) => c.id)).toEqual(["c1"]);
  });
});
