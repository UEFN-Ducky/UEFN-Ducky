// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FolderItem, ProjectFileEntry } from "../types/panel";
import { setProjectContentRoot } from "../verse-editor/utils/isVerseFile";
import { SidebarFileTree } from "./SidebarFileTree";
import { SidebarFolderTree } from "./SidebarFolderTree";

const confirm = vi.hoisted(() => vi.fn(async () => false));
vi.mock("../contexts/ConfirmModalContext", () => ({ useConfirmModal: () => ({ confirm }) }));
vi.mock("./ducky/DuckyAvatars", () => ({ DuckyAvatar: () => null, DUCKY_AVATAR_SIZES: { sidebar: 22 } }));
vi.mock("./editor/ChatTabHoverCard", () => ({ ChatTabHoverCard: ({ children }: { children: unknown }) => children }));
vi.mock("./editor/FolderTabHoverCard", () => ({ FolderTabHoverCard: ({ children }: { children: unknown }) => children }));
vi.mock("./editor/FileTabHoverCard", () => ({ FileTabHoverCard: ({ children }: { children: unknown }) => children }));
vi.mock("../hooks/usePluginContributions", () => ({ usePluginContributions: () => ({}), pluginContributesSettingsTab: () => false }));
vi.mock("../hooks/usePluginUiPrefs", () => ({ usePluginUiPrefs: () => ({ prefs: {}, setPref: () => {} }) }));
vi.mock("../voice/useLiveChatPresence", () => ({ useIsLiveChat: () => false }));
vi.mock("../voice/LiveChatMark", () => ({ LiveChatDot: () => null }));

type Chat = FolderItem["chats"][number];
type Editing = { kind: "folder" | "chat"; id: string; value: string } | null;

const dir = (path: string): ProjectFileEntry => ({ name: path.split("/").pop()!, path, is_dir: true, read_only: false } as ProjectFileEntry);
const file = (path: string): ProjectFileEntry => ({ name: path.split("/").pop()!, path, is_dir: false, read_only: false } as ProjectFileEntry);
const listings: Record<string, ProjectFileEntry[]> = {
  __roots__: [{ name: "Content", path: "Content", is_dir: true, read_only: false, kind: "content" } as ProjectFileEntry],
  Content: [dir("Content/Art"), file("Content/a.verse")],
  "Content/Art": [],
};

const api = {
  get_listener_status: vi.fn(),
  apply_sidebar_layout: vi.fn().mockResolvedValue(undefined),
  group_members: vi.fn(async () => ({ ok: true, members: [] })),
  group_create: vi.fn(async () => ({ ok: true, id: "hub-new", title: "Group", folder_id: "f-new" })),
  list_project_files: vi.fn(async (path: string) => ({ entries: listings[path] ?? [] })),
  list_workspace_roots: vi.fn(async () => []),
  set_import_drop_target: vi.fn(async () => undefined),
  delete_project_entry: vi.fn(async () => ({ trash_token: null })),
};

function ChatTree() {
  const [folders, setFolders] = useState<FolderItem[]>([]);
  const [rootChats, setRootChats] = useState<Chat[]>([
    { id: "a", name: "Alpha" },
    { id: "b", name: "Bravo" },
    { id: "c", name: "Charlie" },
  ]);
  const [editing, setEditing] = useState<Editing>(null);
  return (
    <SidebarFolderTree
      folders={folders} setFolders={setFolders} rootChats={rootChats} setRootChats={setRootChats}
      archiveChats={[]} load={async () => {}} activeChats={[]} runningChatIds={new Set()}
      onChatSelect={() => {}} newlyCreatedIds={new Set()} editing={editing} setEditing={setEditing}
      editInputRef={createRef<HTMLInputElement>()} onCommitRename={() => {}} onCancelRename={() => {}}
      onRenameFolder={() => {}} onDeleteFolder={() => {}} onRenameChat={() => {}} onDeleteChat={() => {}}
      onFocusChat={() => {}} onEditDucky={() => {}} selectedChatFolderId={null} onSelectChatFolder={() => {}}
      onCreateDucky={() => {}} onCreateGroup={() => {}} currentProjectSlug="here"
    />
  );
}

const touch = { pointerType: "touch", pointerId: 1, button: 0 };
const row = (id: string) => document.querySelector(`[data-tree-row="${id}"]`)!;

beforeEach(() => {
  setProjectContentRoot("Content");
  window.pywebview = { api } as unknown as typeof window.pywebview;
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  delete window.pywebview;
});

describe("selection action bar", () => {
  it("chat tree: 3 selected shows the bar; Create group runs the menu's group flow; ✕ clears", async () => {
    render(<ChatTree />);
    expect(screen.queryByRole("toolbar", { name: "Selection actions" })).toBeNull();
    fireEvent.click(screen.getByText("Alpha"));
    fireEvent.click(screen.getByText("Bravo"), { ctrlKey: true });
    fireEvent.click(screen.getByText("Charlie"), { ctrlKey: true });
    expect(screen.getByText("3 selected")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Create group" }));
    await waitFor(() => expect(api.apply_sidebar_layout).toHaveBeenCalledTimes(1));
    expect(api.group_create).toHaveBeenCalledWith("Group", "", false, undefined);
    const patch = api.apply_sidebar_layout.mock.calls[0][0] as { chats: Array<{ id: string; folder_id: string }> };
    expect(patch.chats.filter((c) => c.folder_id === "f-new").map((c) => c.id).sort()).toEqual(["a", "b", "c"]);
    // The group flow clears the selection, so the bar goes away.
    expect(screen.queryByText("3 selected")).toBeNull();

    fireEvent.click(screen.getByText("Alpha"));
    fireEvent.click(screen.getByText("Bravo"), { ctrlKey: true });
    expect(screen.getByText("2 selected")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.queryByText("2 selected")).toBeNull();
  });

  it("file tree: long-press selects a file, a tap adds a folder, and the bar's Delete opens the confirm", async () => {
    render(<SidebarFileTree projectSlug="island" parentPath="Content" onParentPathChange={() => {}} onFileSelect={() => {}} />);
    await waitFor(() => expect(row("file:Content/a.verse")).toBeTruthy());
    const fileRow = row("file:Content/a.verse");
    fireEvent.pointerDown(fileRow, touch);
    await act(() => new Promise((resolve) => setTimeout(resolve, 470)));
    fireEvent.pointerUp(fileRow, touch);
    fireEvent.click(fileRow);
    const artRow = row("dir:Content/Art");
    fireEvent.pointerDown(artRow, touch);
    fireEvent.pointerUp(artRow, touch);
    fireEvent.click(artRow);
    expect(screen.getByText("2 selected")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(confirm.mock.calls[0][0]).toMatchObject({ message: "Delete all 2 selected items?", confirmLabel: "Delete ALL" });
    expect(api.delete_project_entry).not.toHaveBeenCalled();
  });
});
