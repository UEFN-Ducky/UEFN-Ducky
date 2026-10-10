import { describe, expect, it } from "vitest";
import type { FolderItem } from "../types/panel";
import { resolveDropTarget, TREE_ROOT } from "../tree-dnd/treeMove";
import {
  buildDuckiesIndex,
  duckiesCreateTarget,
  duckiesDropPolicy,
  duckiesLayoutFor,
  duckiesPlaces,
  planDuckiesDrop,
  planDuckiesRestore,
  projectNodeId,
  removeFromDuckies,
  type DuckiesTreeData,
} from "./duckiesTreeModel";
import { projectFolderId, wrapProjectsAsFolders } from "./sidebarTree";

type Chat = FolderItem["chats"][number];
const chat = (id: string, extra: Partial<Chat> = {}): Chat => ({ id, name: id, ...extra });
function folder(id: string, chats: Chat[] = [], children: FolderItem[] = [], extra: Partial<FolderItem> = {}): FolderItem {
  return { id, name: id, parentId: "", sortOrder: 0, expanded: true, chats, children, ...extra };
}

/** One island open ("here"): its chats and folders at the top, Global Agents beside. */
function oneProject(): DuckiesTreeData {
  const global = wrapProjectsAsFolders(
    [{
      slug: "_no_project",
      name: "No project",
      folders: [folder("ga-group", [chat("ga-member")], [folder("ga-sub", [chat("ga-deep")])], { groupHubId: "ga-hub" })],
      rootChats: [chat("ga-loose")],
    }],
    "here",
    new Map(),
  );
  return {
    folders: [folder("squad", [chat("m1"), chat("m2")], [folder("inner", [chat("i1")])], { groupHubId: "hub" }), ...global],
    rootChats: [chat("c1"), chat("c2")],
  };
}

const ids = (chats: Chat[]) => chats.map((c) => c.id);

describe("Duckies moves", () => {
  it("reorders between rows and saves that project's layout", () => {
    const data = oneProject();
    const index = buildDuckiesIndex(data, "here");
    const policy = duckiesDropPolicy(index);
    const target = resolveDropTarget(index.model, ["chat:c2"], "chat:c1", "before", policy)!;
    const plan = planDuckiesDrop(index, data, ["chat:c2"], target, policy);
    expect(ids(plan.data.rootChats)).toEqual(["c2", "c1"]);
    expect(plan.projectMoves).toEqual([]);
    expect(plan.layoutSlugs).toEqual(["here"]);
    const patch = duckiesLayoutFor(plan.data, "here", "here")!;
    expect(patch.chats.filter((c) => !c.folder_id).map((c) => c.id)).toEqual(["c2", "c1"]);
    expect(patch.folders.map((f) => f.id)).toEqual(["squad", "inner"]);
  });

  it("drops into a group, and a group moves with its members and sub-folders", () => {
    const data = oneProject();
    const index = buildDuckiesIndex(data, "here");
    const policy = duckiesDropPolicy(index);
    const into = resolveDropTarget(index.model, ["chat:c1"], "folder:squad", "into", policy)!;
    const joined = planDuckiesDrop(index, data, ["chat:c1"], into, policy).data;
    expect(ids(joined.folders[0].chats)).toEqual(["m1", "m2", "c1"]);

    // The squad group goes into Global Agents' group: members and nested folder too.
    const nest = resolveDropTarget(index.model, ["folder:squad"], "folder:ga-group", "into", policy)!;
    const plan = planDuckiesDrop(index, data, ["folder:squad"], nest, policy);
    const ga = plan.data.folders.find((f) => f.id === projectFolderId("_no_project"))!;
    const squad = ga.children[0].children.find((f) => f.id === "squad")!;
    expect(ids(squad.chats)).toEqual(["m1", "m2"]);
    expect(squad.children.map((f) => f.id)).toEqual(["inner"]);
    expect(squad.projectSlug).toBe("_no_project");
    // Changing project goes through the host's project move, into that group.
    expect(plan.projectMoves).toEqual([{ slug: "_no_project", folderId: "ga-group", convIds: [], folderIds: ["squad"] }]);
    expect(plan.layoutSlugs).toEqual(["_no_project"]);
  });

  it("never nests a group into itself or into its own sub-folder", () => {
    const data = oneProject();
    const index = buildDuckiesIndex(data, "here");
    const policy = duckiesDropPolicy(index);
    expect(resolveDropTarget(index.model, ["folder:squad"], "folder:squad", "into", policy)).toBeNull();
    expect(resolveDropTarget(index.model, ["folder:squad"], "folder:inner", "into", policy)).toBeNull();
    expect(resolveDropTarget(index.model, ["folder:squad"], "chat:i1", "before", policy)).toBeNull();
  });

  it("Global Agents takes single chats, groups and folders from any island", () => {
    const data = oneProject();
    const index = buildDuckiesIndex(data, "here");
    const policy = duckiesDropPolicy(index);
    const globalRow = projectNodeId("_no_project");
    // A project row only takes drops into it, wherever on the row the pointer is.
    const chatTarget = resolveDropTarget(index.model, ["chat:c1"], globalRow, "before", policy)!;
    expect(chatTarget.parentId).toBe(globalRow);
    expect(planDuckiesDrop(index, data, ["chat:c1"], chatTarget, policy).projectMoves).toEqual([
      { slug: "_no_project", folderId: "", convIds: ["c1"], folderIds: [] },
    ]);
    const groupTarget = resolveDropTarget(index.model, ["folder:squad"], globalRow, "into", policy)!;
    const plan = planDuckiesDrop(index, data, ["folder:squad"], groupTarget, policy);
    const ga = plan.data.folders.find((f) => f.id === projectFolderId("_no_project"))!;
    expect(ga.children.map((f) => f.id)).toEqual(["ga-group", "squad"]);
    expect(ids(ga.chats)).toEqual(["ga-loose"]);
    // Reordering inside Global Agents is a plain layout save of that bucket.
    const reorder = resolveDropTarget(index.model, ["folder:ga-sub"], globalRow, "into", policy)!;
    const moved = planDuckiesDrop(index, data, ["folder:ga-sub"], reorder, policy);
    expect(moved.projectMoves).toEqual([]);
    expect(duckiesLayoutFor(moved.data, "_no_project", "here")!.folders.map((f) => f.id)).toEqual(["ga-group", "ga-sub"]);
  });

  it("blank space drops onto the open island; the top level itself takes nothing", () => {
    const data = oneProject();
    const index = buildDuckiesIndex(data, "here");
    const policy = duckiesDropPolicy(index);
    const target = resolveDropTarget(index.model, ["chat:ga-loose"], null, "into", policy)!;
    expect(target.parentId).toBe(index.currentRootId);
    expect(planDuckiesDrop(index, data, ["chat:ga-loose"], target, policy).projectMoves).toEqual([
      { slug: "here", folderId: "", convIds: ["ga-loose"], folderIds: [] },
    ]);
    expect(policy.acceptsDrop?.(TREE_ROOT, ["chat:c1"])).toBe(false);
  });

  it("undo puts a moved group back in its project and place", () => {
    const data = oneProject();
    const index = buildDuckiesIndex(data, "here");
    const policy = duckiesDropPolicy(index);
    const places = duckiesPlaces(index, ["folder:squad", "chat:c2"]);
    const target = resolveDropTarget(index.model, ["folder:squad", "chat:c2"], projectNodeId("_no_project"), "into", policy)!;
    const after = planDuckiesDrop(index, data, ["folder:squad", "chat:c2"], target, policy).data;
    const back = planDuckiesRestore(buildDuckiesIndex(after, "here"), after, places);
    expect(ids(back.data.rootChats)).toEqual(["c1", "c2"]);
    expect(back.data.folders.map((f) => f.id)[0]).toBe("squad");
    expect(back.projectMoves).toEqual([{ slug: "here", folderId: "", convIds: ["c2"], folderIds: ["squad"] }]);
    expect(back.layoutSlugs.sort()).toEqual(["here"]);
  });
});

describe("deleted rows leave the tree at once", () => {
  it("drops deleted chats and folders; a removed group's duckies stay unless archived with it", () => {
    const data = oneProject();
    const kept = removeFromDuckies(data, { chatIds: ["c1", "ga-member"], folderIds: ["squad"] });
    expect(ids(kept.rootChats)).toEqual(["c2", "m1", "m2"]);
    expect(kept.folders.map((f) => f.id)).toEqual([projectFolderId("_no_project"), "inner"]);
    const ga = kept.folders[0];
    expect(ids(ga.children[0].chats)).toEqual([]);
    const gone = removeFromDuckies(data, { folderIds: ["squad"], keepContents: false });
    expect(ids(gone.rootChats)).toEqual(["c1", "c2"]);
    expect(gone.folders.map((f) => f.id)).toEqual([projectFolderId("_no_project")]);
    expect(removeFromDuckies(data, {})).toBe(data);
  });
});

describe("creating inside a folder", () => {
  it("creates in Global Agents and other islands with their project", () => {
    const data = oneProject();
    expect(duckiesCreateTarget(projectFolderId("_no_project"), "here", data.folders)).toEqual({ folderId: "", projectSlug: "_no_project" });
    expect(duckiesCreateTarget("ga-group", "here", data.folders)).toEqual({ folderId: "ga-group", projectSlug: "_no_project" });
    expect(duckiesCreateTarget("squad", "here", data.folders)).toEqual({ folderId: "squad" });
    expect(duckiesCreateTarget(projectFolderId("here"), "here", data.folders)).toEqual({ folderId: "" });
    expect(duckiesCreateTarget("", "here", data.folders)).toEqual({ folderId: "" });
  });
});
