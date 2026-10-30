// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { createRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FolderItem } from "../types/panel";
import { applyWorkflowEvent, resetWorkflowRunsForTests } from "../hooks/workflowRunsByChat";
import { SidebarFolderTree } from "./SidebarFolderTree";

vi.mock("./ducky/DuckyAvatars", () => ({ DuckyAvatar: () => <i className="avatar-stub" />, DUCKY_AVATAR_SIZES: { sidebar: 22 } }));
vi.mock("./editor/ChatTabHoverCard", () => ({ ChatTabHoverCard: ({ children }: { children: unknown }) => children }));
vi.mock("./editor/FolderTabHoverCard", () => ({ FolderTabHoverCard: ({ children }: { children: unknown }) => children }));
vi.mock("../hooks/usePluginContributions", () => ({ usePluginContributions: () => ({}), pluginContributesSettingsTab: () => false }));
vi.mock("../hooks/usePluginUiPrefs", () => ({ usePluginUiPrefs: () => ({ prefs: {}, setPref: () => {} }) }));
vi.mock("../voice/useLiveChatPresence", () => ({ useIsLiveChat: () => false }));
vi.mock("../voice/LiveChatMark", () => ({ LiveChatDot: () => null }));

type Chat = FolderItem["chats"][number];
const members: Chat[] = ["m1", "m2"].map((id) => ({ id, name: `Stub ${id}`, parentConvId: "hub" }));
const group = (expanded: boolean): FolderItem => ({
  id: "g", name: "Deploy", parentId: "", sortOrder: 0, expanded, chats: members, children: [], groupHubId: "hub",
});

function Harness({ expanded }: { expanded: boolean }) {
  const [folders, setFolders] = useState<FolderItem[]>([group(expanded)]);
  const [rootChats, setRootChats] = useState<Chat[]>([]);
  const [editing, setEditing] = useState<{ kind: "folder" | "chat"; id: string; value: string } | null>(null);
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

const api = {
  get_listener_status: vi.fn(),
  apply_sidebar_layout: vi.fn().mockResolvedValue(undefined),
  group_members: vi.fn(async () => ({ ok: true, members: [] })),
  get_plan: vi.fn(async () => ({ ok: false })),
};

const start = () => applyWorkflowEvent({
  type: "workflow_run", state: "started", conv: "m1", id: "w", run: "r", name: "Ship DuckyOS changes",
  plan: [{ node: "a", label: "A" }, { node: "b", label: "B" }],
} as Parameters<typeof applyWorkflowEvent>[0]);
const finish = () => applyWorkflowEvent({ type: "workflow_run", state: "done", id: "w", run: "r" } as Parameters<typeof applyWorkflowEvent>[0]);
const row = (id: string) => document.querySelector(`[data-sidebar-id="${id}"]`) as HTMLElement;

beforeEach(() => {
  window.pywebview = { api } as unknown as typeof window.pywebview;
  resetWorkflowRunsForTests();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  delete window.pywebview;
});

describe("sidebar marks chats running a workflow", () => {
  it("a chat with an idle agent shows the workflow ring while its run lasts", async () => {
    render(<Harness expanded />);
    await screen.findByText("Stub m1");
    act(start);
    const ring = row("chat:m1").querySelector(".sidebar-workflow-spinner")!;
    expect(ring).toBeTruthy();
    expect(ring.getAttribute("title")).toBe("Workflow: Ship DuckyOS changes · step 1/2");
    expect(row("chat:m1").querySelector(".sidebar-agent-spinner")).toBeNull();
    expect(row("chat:m2").querySelector(".sidebar-workflow-spinner")).toBeNull();
    act(finish);
    expect(row("chat:m1").querySelector(".sidebar-workflow-spinner")).toBeNull();
    expect(row("chat:m1").querySelector(".avatar-stub")).toBeTruthy();
  });

  it("a collapsed group shows the running dot for a workflow but no Stop count", async () => {
    render(<Harness expanded={false} />);
    await screen.findByText("Deploy");
    const branch = () => screen.getByText("Deploy").closest(".sidebar-tree-branch")!;
    expect(branch().querySelector(".sidebar-agent-spinner")).toBeNull();
    act(start);
    expect(branch().querySelector(".sidebar-agent-spinner")).toBeTruthy();
    expect(screen.queryByText(/running$/)).toBeNull();
    act(finish);
    expect(branch().querySelector(".sidebar-agent-spinner")).toBeNull();
  });
});
