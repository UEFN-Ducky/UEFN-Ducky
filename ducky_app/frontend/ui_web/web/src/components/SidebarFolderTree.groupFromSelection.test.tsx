// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FolderItem } from "../types/panel";
import { SidebarFolderTree } from "./SidebarFolderTree";

vi.mock("./ducky/DuckyAvatars", () => ({ DuckyAvatar: () => null, DUCKY_AVATAR_SIZES: { sidebar: 22 } }));
vi.mock("./editor/ChatTabHoverCard", () => ({ ChatTabHoverCard: ({ children }: { children: unknown }) => children }));
vi.mock("./editor/FolderTabHoverCard", () => ({ FolderTabHoverCard: ({ children }: { children: unknown }) => children }));
vi.mock("../hooks/usePluginContributions", () => ({ usePluginContributions: () => ({}), pluginContributesSettingsTab: () => false }));
vi.mock("../hooks/usePluginUiPrefs", () => ({ usePluginUiPrefs: () => ({ prefs: {}, setPref: () => {} }) }));
vi.mock("../voice/useLiveChatPresence", () => ({ useIsLiveChat: () => false }));
vi.mock("../voice/LiveChatMark", () => ({ LiveChatDot: () => null }));

type Chat = FolderItem["chats"][number];
type Editing = { kind: "folder" | "chat"; id: string; value: string } | null;

const api = {
  get_listener_status: vi.fn(),
  apply_sidebar_layout: vi.fn().mockResolvedValue(undefined),
  group_members: vi.fn(async () => ({ ok: true, members: [] })),
  group_create: vi.fn(async () => ({ ok: true, id: "hub-new", title: "Group", folder_id: "f-new" })),
};

let editingSeen: Editing = null;

function Harness({ archive = [] as Chat[] }) {
  const [folders, setFolders] = useState<FolderItem[]>([]);
  const [rootChats, setRootChats] = useState<Chat[]>([
    { id: "a", name: "Alpha" },
    { id: "b", name: "Bravo" },
    { id: "c", name: "Charlie" },
    { id: "d", name: "Delta" },
  ]);
  const [editing, setEditing] = useState<Editing>(null);
  editingSeen = editing;
  return (
    <SidebarFolderTree
      folders={folders} setFolders={setFolders} rootChats={rootChats} setRootChats={setRootChats}
      archiveChats={archive} load={async () => {}} activeChats={[]} runningChatIds={new Set()}
      onChatSelect={() => {}} newlyCreatedIds={new Set()} editing={editing} setEditing={setEditing}
      editInputRef={createRef<HTMLInputElement>()} onCommitRename={() => {}} onCancelRename={() => {}}
      onRenameFolder={() => {}} onDeleteFolder={() => {}} onRenameChat={() => {}} onDeleteChat={() => {}}
      onFocusChat={() => {}} onEditDucky={() => {}} selectedChatFolderId={null} onSelectChatFolder={() => {}}
      onCreateDucky={() => {}} onCreateGroup={() => {}} currentProjectSlug="here"
    />
  );
}

beforeEach(() => {
  window.pywebview = { api } as unknown as typeof window.pywebview;
  editingSeen = null;
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  delete window.pywebview;
});

describe("Create group from the selection", () => {
  it("moves exactly the 3 selected chats into one new group and starts its rename", async () => {
    render(<Harness />);
    fireEvent.click(screen.getByText("Alpha"));
    fireEvent.click(screen.getByText("Bravo"), { ctrlKey: true });
    fireEvent.click(screen.getByText("Charlie"), { ctrlKey: true });
    fireEvent.contextMenu(screen.getByText("Bravo"));
    fireEvent.click(await screen.findByText("Create group with these 3"));

    await waitFor(() => expect(api.apply_sidebar_layout).toHaveBeenCalledTimes(1));
    expect(api.group_create).toHaveBeenCalledTimes(1);
    expect(api.group_create).toHaveBeenCalledWith("Group", "", false, undefined);
    const patch = api.apply_sidebar_layout.mock.calls[0][0] as { chats: Array<{ id: string; folder_id: string }> };
    const inGroup = patch.chats.filter((c) => c.folder_id === "f-new").map((c) => c.id).sort();
    expect(inGroup).toEqual(["a", "b", "c"]);
    expect(patch.chats.find((c) => c.id === "d")?.folder_id).toBe("");
    expect(editingSeen).toEqual({ kind: "folder", id: "f-new", value: "Group" });
  });

  it("touch: long-press starts selection mode, then taps toggle rows instead of opening them", async () => {
    vi.useFakeTimers();
    try {
      render(<Harness />);
      const touch = { pointerType: "touch", pointerId: 1, button: 0 };
      const alpha = screen.getByText("Alpha");
      fireEvent.pointerDown(alpha, touch);
      act(() => vi.advanceTimersByTime(450));
      fireEvent.pointerUp(alpha, touch);
      fireEvent.click(alpha);
      for (const name of ["Bravo", "Charlie"]) {
        fireEvent.pointerDown(screen.getByText(name), touch);
        fireEvent.pointerUp(screen.getByText(name), touch);
        fireEvent.click(screen.getByText(name));
      }
      fireEvent.contextMenu(screen.getByText("Bravo"));
      expect(screen.getByText("Create group with these 3")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("a single chat offers the single label", async () => {
    render(<Harness />);
    fireEvent.contextMenu(screen.getByText("Delta"));
    expect(await screen.findByText("Create group with this chat")).toBeTruthy();
    expect(screen.queryByText(/Create group with these/)).toBeNull();
  });
});
