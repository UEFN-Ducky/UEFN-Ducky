// @vitest-environment jsdom
import { act, cleanup, createEvent, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FolderItem } from "../types/panel";
import { projectFolderId, wrapProjectsAsFolders } from "../utils/sidebarTree";
import { SidebarFolderTree } from "./SidebarFolderTree";

vi.mock("./ducky/DuckyAvatars", () => ({ DuckyAvatar: () => null, DUCKY_AVATAR_SIZES: { sidebar: 22 } }));
vi.mock("./editor/ChatTabHoverCard", () => ({ ChatTabHoverCard: ({ children }: { children: unknown }) => children }));
vi.mock("./editor/FolderTabHoverCard", () => ({ FolderTabHoverCard: ({ children }: { children: unknown }) => children }));
vi.mock("../hooks/usePluginContributions", () => ({ usePluginContributions: () => ({}), pluginContributesSettingsTab: () => false }));
vi.mock("../hooks/usePluginUiPrefs", () => ({ usePluginUiPrefs: () => ({ prefs: {}, setPref: () => {} }) }));
vi.mock("../voice/useLiveChatPresence", () => ({ useIsLiveChat: () => false }));
vi.mock("../voice/LiveChatMark", () => ({ LiveChatDot: () => null }));

type Chat = FolderItem["chats"][number];
const chat = (id: string): Chat => ({ id, name: id });
function folder(id: string, chats: Chat[] = [], children: FolderItem[] = [], extra: Partial<FolderItem> = {}): FolderItem {
  return { id, name: id, parentId: "", sortOrder: 0, expanded: true, chats, children, ...extra };
}

const api = {
  get_listener_status: vi.fn(),
  apply_sidebar_layout: vi.fn().mockResolvedValue(undefined),
  move_chats_to_project: vi.fn().mockResolvedValue(undefined),
};

function initial() {
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
    folders: [folder("squad", [chat("m1")], [], { groupHubId: "hub" }), ...global],
    rootChats: [chat("c1"), chat("c2")],
  };
}

function Harness({ showGlobalAgents = true }: { showGlobalAgents?: boolean }) {
  const start = initial();
  const [folders, setFolders] = useState(start.folders);
  const [rootChats, setRootChats] = useState(start.rootChats);
  const [editing, setEditing] = useState<{ kind: "folder" | "chat"; id: string; value: string } | null>(null);
  return (
    <SidebarFolderTree
      folders={folders}
      setFolders={setFolders}
      rootChats={rootChats}
      setRootChats={setRootChats}
      archiveChats={[]}
      load={async () => {}}
      activeChats={[]}
      runningChatIds={new Set()}
      onChatSelect={() => {}}
      newlyCreatedIds={new Set()}
      editing={editing}
      setEditing={setEditing}
      editInputRef={createRef<HTMLInputElement>()}
      onCommitRename={() => {}}
      onCancelRename={() => {}}
      onRenameFolder={() => {}}
      onDeleteFolder={() => {}}
      onRenameChat={() => {}}
      onDeleteChat={() => {}}
      onFocusChat={() => {}}
      onEditDucky={() => {}}
      selectedChatFolderId={null}
      onSelectChatFolder={() => {}}
      onCreateDucky={() => {}}
      onCreateGroup={() => {}}
      currentProjectSlug="here"
      showGlobalAgents={showGlobalAgents}
    />
  );
}

const transfer = () => ({ setData: vi.fn(), getData: vi.fn(), setDragImage: vi.fn(), effectAllowed: "", dropEffect: "", types: [] as string[] });
type Transfer = ReturnType<typeof transfer>;

/** Native drag events with a pointer height (jsdom has no DragEvent). */
function drag(kind: "dragStart" | "dragOver" | "drop" | "dragEnd", target: Element, dataTransfer: Transfer, clientY = 0) {
  const event = createEvent[kind](target, { dataTransfer });
  Object.defineProperty(event, "clientY", { value: clientY });
  Object.defineProperty(event, "clientX", { value: 40 });
  fireEvent(target, event);
}

/** Drag `from` and let go on `to` at `y` px into its 20px-high row. */
function dragTo(from: Element, to: Element, y: number) {
  const dt = transfer();
  drag("dragStart", from, dt, 5);
  drag("dragOver", to, dt, y);
  drag("drop", to, dt, y);
  drag("dragEnd", from, dt, y);
  return dt;
}

const row = (id: string) => document.querySelector(`[data-tree-row="${id}"]`)!;

beforeEach(() => {
  window.pywebview = { api } as unknown as typeof window.pywebview;
  // Every row is 20px high at the top of its box.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    top: 0, bottom: 20, height: 20, left: 0, right: 240, width: 240, x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  api.apply_sidebar_layout.mockClear();
  api.move_chats_to_project.mockClear();
  delete window.pywebview;
});

describe("Duckies tree on the shared drag engine", () => {
  it("drops between rows: one layout save, in the new order, and the row moves at once", async () => {
    render(<Harness />);
    const dt = dragTo(row("chat:c2"), row("chat:c1"), 2);
    expect(dt.setDragImage).toHaveBeenCalledTimes(1); // the light floating preview
    await waitFor(() => expect(api.apply_sidebar_layout).toHaveBeenCalledTimes(1));
    const patch = api.apply_sidebar_layout.mock.calls[0][0];
    expect(patch.project_slug).toBe("here");
    expect(patch.chats.filter((c: { folder_id: string }) => !c.folder_id).map((c: { id: string }) => c.id)).toEqual(["c2", "c1"]);
    const order = [...document.querySelectorAll("[data-tree-row^='chat:c']")].map((el) => el.getAttribute("data-tree-row"));
    expect(order).toEqual(["chat:c2", "chat:c1"]);
    expect(api.move_chats_to_project).not.toHaveBeenCalled();
  });

  it("shows the insertion line exactly where the row will land while dragging", () => {
    render(<Harness />);
    const dt = transfer();
    drag("dragStart", row("chat:c1"), dt, 5);
    drag("dragOver", row("chat:c2"), dt, 18);
    // Below c2 (the last chat) is the slot above the first folder.
    expect(document.querySelector("[data-tree-node='folder:squad']")!.classList.contains("tree-drop-before")).toBe(true);
    drag("dragOver", row("folder:squad"), dt, 10);
    expect(row("folder:squad").classList.contains("tree-drop-into")).toBe(true);
    expect(document.querySelector(".tree-drop-before")).toBeNull();
    drag("dragEnd", row("chat:c1"), dt, 10);
    expect(document.querySelector(".tree-drop-into, .tree-drop-before, .tree-drop-after, .tree-drag-source")).toBeNull();
    expect(api.apply_sidebar_layout).not.toHaveBeenCalled();
  });

  it("never lets a group drop into itself", () => {
    render(<Harness />);
    const dt = transfer();
    drag("dragStart", row("folder:ga-group"), dt, 5);
    drag("dragOver", row("folder:ga-sub"), dt, 10);
    expect(document.querySelector(".tree-drop-into")).toBeNull();
    expect(dt.dropEffect).toBe("none");
    drag("drop", row("folder:ga-sub"), dt, 10);
    expect(api.apply_sidebar_layout).not.toHaveBeenCalled();
    expect(api.move_chats_to_project).not.toHaveBeenCalled();
  });
});

describe("Global Agents is a real folder", () => {
  it("shows groups with their members, sub-folders and single chats", () => {
    render(<Harness />);
    expect(screen.getByRole("button", { name: "Global Agents" })).toBeTruthy();
    for (const id of ["folder:ga-group", "chat:ga-member", "folder:ga-sub", "chat:ga-deep", "chat:ga-loose"]) {
      expect(row(id)).toBeTruthy();
    }
    // Inside the Global Agents block, not loose in the island's list.
    const block = document.querySelector(`[data-tree-node="folder:${projectFolderId("_no_project")}"]`)!;
    expect(block.contains(row("chat:ga-member"))).toBe(true);
  });

  it("takes a chat into one of its groups from the open island (one project move)", async () => {
    render(<Harness />);
    dragTo(row("chat:c1"), row("folder:ga-group"), 10);
    await waitFor(() => expect(api.move_chats_to_project).toHaveBeenCalledTimes(1));
    expect(api.move_chats_to_project).toHaveBeenCalledWith(["c1"], [], "_no_project", "ga-group");
    await waitFor(() => expect(api.apply_sidebar_layout).toHaveBeenCalledTimes(1));
    expect(api.apply_sidebar_layout.mock.calls[0][0].project_slug).toBe("_no_project");
    // Shown there right away.
    const group = document.querySelector("[data-tree-node='folder:ga-group']")!;
    expect(group.contains(row("chat:c1"))).toBe(true);
  });

  it("takes a whole group, members and all", async () => {
    render(<Harness />);
    dragTo(row("folder:squad"), row(`folder:${projectFolderId("_no_project")}`), 2);
    await waitFor(() => expect(api.move_chats_to_project).toHaveBeenCalledTimes(1));
    expect(api.move_chats_to_project).toHaveBeenCalledWith([], ["squad"], "_no_project", "");
    const global = document.querySelector(`[data-tree-node="folder:${projectFolderId("_no_project")}"]`)!;
    expect(global.contains(row("folder:squad"))).toBe(true);
    expect(global.contains(row("chat:m1"))).toBe(true);
  });

  it("reorders inside itself with a plain layout save of that folder", async () => {
    render(<Harness />);
    dragTo(row("folder:ga-sub"), row(`folder:${projectFolderId("_no_project")}`), 10);
    await waitFor(() => expect(api.apply_sidebar_layout).toHaveBeenCalledTimes(1));
    const patch = api.apply_sidebar_layout.mock.calls[0][0];
    expect(patch.project_slug).toBe("_no_project");
    expect(patch.folders).toEqual(expect.arrayContaining([{ id: "ga-sub", parent_id: "", sort_order: 1 }]));
    expect(api.move_chats_to_project).not.toHaveBeenCalled();
  });

  it("can be hidden from the header menu", () => {
    render(<Harness showGlobalAgents={false} />);
    expect(screen.queryByRole("button", { name: "Global Agents" })).toBeNull();
    expect(row("chat:c1")).toBeTruthy();
  });
});

describe("the Duckies tree is Ctrl+Z's scope", () => {
  it("is focusable and marked as the Duckies history", () => {
    render(<Harness />);
    const tree = document.querySelector("[data-undo-scope='chats']") as HTMLElement;
    expect(tree).toBeTruthy();
    act(() => tree.focus());
    expect(document.activeElement).toBe(tree);
  });
});
