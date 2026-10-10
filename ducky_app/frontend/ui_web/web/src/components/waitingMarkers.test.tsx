// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetAskUserForTests, listAskUserSessions, runAskUser, settleAskUser } from "../ask-user/runAskUser";
import { _resetFocusedChatForAsk } from "../ask-user/focusedChatForAsk";
import { _resetBackgroundActivityForTests } from "../hooks/backgroundActivity";
import { pushLocalAgentEvent } from "../hooks/useAgentEventBus";
import { _resetWaitingChatsForTests } from "../hooks/waitingChats";
import { resetWorkflowRunsForTests } from "../hooks/workflowRunsByChat";
import { registerOpenChatReference } from "../navigation/openChatReference";
import { subscribePendingCard } from "../navigation/pendingCard";
import { _resetTerminalApprovalsForTests, installTerminalApprovals } from "../terminal/terminalApprovals";
import type { ChatTab, EditorTab, FolderItem } from "../types/panel";
import { BackgroundActivityDropdown } from "./BackgroundActivityDropdown";
import { EditorTabs } from "./EditorTabs";
import { SidebarFolderTree } from "./SidebarFolderTree";

const api = vi.hoisted(() => ({
  list_all_conversations: vi.fn(),
  list_running_agents: vi.fn(),
  terminal_pending_commands: vi.fn(),
  terminal_approve_command: vi.fn(),
  terminal_reject_command: vi.fn(),
  workflow_run_snapshot: vi.fn(),
}));
vi.mock("../hooks/usePanelApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../hooks/usePanelApi")>()),
  getApi: () => api,
}));

if (typeof Element.prototype.scrollIntoView !== "function") {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}
if (!("ResizeObserver" in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

const question = [{ id: "q", prompt: "Which map?", options: [{ id: "a", label: "A" }] }];
const command = (request_id: string, conv_id: string, cmd = "npm test") => ({
  type: "terminal_command_pending" as const,
  request_id,
  session_id: "s1",
  command: cmd,
  conv_id,
  rule_label: conv_id ? "npm test" : "",
});

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
}

const chat = (id: string, name: string): ChatTab => ({ id, name });
const noop = () => {};

let opened: string[] = [];
let unregister: () => void = noop;

beforeEach(() => {
  api.list_all_conversations.mockResolvedValue([
    { id: "c1", title: "Builder" },
    { id: "c2", title: "Tester" },
  ]);
  api.list_running_agents.mockResolvedValue([]);
  api.terminal_pending_commands.mockResolvedValue([]);
  api.terminal_approve_command.mockResolvedValue({ ok: true });
  api.terminal_reject_command.mockResolvedValue({ ok: true });
  api.workflow_run_snapshot.mockResolvedValue({ ok: true, events: [] });
  installTerminalApprovals();
  opened = [];
  unregister = registerOpenChatReference((target) => opened.push(target.id));
});

afterEach(() => {
  cleanup();
  unregister();
  _resetAskUserForTests();
  _resetTerminalApprovalsForTests();
  _resetWaitingChatsForTests();
  _resetFocusedChatForAsk();
  _resetBackgroundActivityForTests();
  resetWorkflowRunsForTests();
  vi.resetAllMocks();
});

describe("waiting for your answer markers", () => {
  it("the chat's tab wears the marker instead of its icon; clicking it opens the chat at the card", async () => {
    const tabs: EditorTab[] = [
      { id: "chat:c1", kind: "chat", name: "Builder", chatId: "c1" },
      { id: "chat:c2", kind: "chat", name: "Tester", chatId: "c2" },
    ];
    render(
      <EditorTabs
        groupId="g"
        tabs={tabs}
        activeTabId="chat:c2"
        closeTab={noop}
        onActivate={noop}
        runningChatIds={new Set(["c1", "c2"])}
      />,
    );
    expect(document.querySelector("[data-waiting-marker]")).toBeNull();
    act(() => { void runAskUser(question, "", "c1"); });
    const marker = await waitFor(() => {
      const el = document.querySelector('[data-tab-id="chat:c1"] [data-waiting-marker="c1"]');
      expect(el).toBeTruthy();
      return el as HTMLElement;
    });
    expect(document.querySelector('[data-tab-id="chat:c2"] [data-waiting-marker]')).toBeNull();

    const scrolled: string[] = [];
    const stop = subscribePendingCard("c1", () => scrolled.push("c1"));
    fireEvent.click(marker);
    expect(opened).toEqual(["c1"]);
    expect(scrolled).toEqual(["c1"]);
    stop();

    // Answered (here or anywhere): the marker goes.
    act(() => settleAskUser({ ok: true, answers: { q: { selected: ["a"], text: "", skipped: false } } }, listAskUserSessions()[0].id));
    await waitFor(() => expect(document.querySelector("[data-waiting-marker]")).toBeNull());
  });

  it("a sidebar row shows the marker instead of the spinner, and a folded group shows it for its member", async () => {
    const group: FolderItem = {
      id: "g1",
      name: "Team",
      parentId: "",
      sortOrder: 0,
      expanded: false,
      groupHubId: "hub",
      chats: [chat("m1", "Member")],
      children: [],
    };
    render(
      <SidebarFolderTree
        folders={[group]}
        setFolders={noop}
        rootChats={[chat("c1", "Builder"), chat("c2", "Tester")]}
        setRootChats={noop}
        archiveChats={[]}
        setArchiveChats={noop}
        load={async () => {}}
        activeChats={[]}
        runningChatIds={new Set(["c1", "c2", "m1"])}
        onChatSelect={noop}
        newlyCreatedIds={new Set()}
        editing={null}
        setEditing={noop}
        editInputRef={createRef<HTMLInputElement>()}
        onCommitRename={noop}
        onCancelRename={noop}
        onRenameFolder={noop}
        onDeleteFolder={noop}
        onRenameChat={noop}
        onDeleteChat={noop}
        onFocusChat={noop}
        onEditDucky={noop}
        selectedChatFolderId={null}
        onSelectChatFolder={noop}
        onCreateDucky={noop}
        onCreateGroup={noop}
      />,
    );
    const row = (id: string) => document.querySelector(`[data-sidebar-id="${id}"]`) as HTMLElement;
    expect(row("chat:c1").querySelector(".sidebar-agent-spinner")).toBeTruthy();

    act(() => {
      pushLocalAgentEvent(command("r1", "c1"));
      void runAskUser(question, "", "m1");
    });
    await flush();
    expect(row("chat:c1").querySelector('[data-waiting-marker="c1"]')).toBeTruthy();
    expect(row("chat:c1").querySelector(".sidebar-agent-spinner")).toBeNull();
    expect(row("chat:c2").querySelector(".sidebar-agent-spinner")).toBeTruthy();
    expect(row("chat:c2").querySelector("[data-waiting-marker]")).toBeNull();
    expect(row("folder:g1").querySelector('[data-waiting-marker="m1"]')).toBeTruthy();

    fireEvent.click(row("folder:g1").querySelector("[data-waiting-marker]")!);
    expect(opened).toEqual(["m1"]);

    act(() => pushLocalAgentEvent({ type: "terminal_command_decided", request_id: "r1", conv_id: "c1" }));
    await flush();
    expect(row("chat:c1").querySelector("[data-waiting-marker]")).toBeNull();
    expect(row("chat:c1").querySelector(".sidebar-agent-spinner")).toBeTruthy();
  });

  it("the header list shows a Waiting for you section first, and answers a card no chat owns", async () => {
    render(<BackgroundActivityDropdown />);
    await waitFor(() => expect(api.list_all_conversations).toHaveBeenCalled());
    await flush();
    act(() => {
      void runAskUser(question, "", "c1");
      pushLocalAgentEvent(command("r2", "c2"));
      pushLocalAgentEvent(command("r3", "", "npm run release"));
    });
    await flush();
    const trigger = screen.getByLabelText("Background activity");
    expect(trigger.textContent).toContain("3 waiting");
    fireEvent.click(trigger);

    const section = screen.getByLabelText("Waiting for you");
    const heads = [...document.querySelectorAll(".bg-activity-menu .connection-status-menu-head")].map((el) => el.textContent);
    expect(heads[0]).toBe("Waiting for you");
    expect(section.textContent).toContain("Builder");
    expect(section.textContent).toContain("Question: Which map?");
    expect(section.textContent).toContain("Tester");
    expect(section.textContent).toContain("Command: npm test");

    // No chat asked this one: answered right in the list, with no "in this chat" choices.
    const loose = section.querySelector('[data-approval-request="r3"]') as HTMLElement;
    const labels = [...loose.querySelectorAll(".ask-user-option-label")].map((el) => el.textContent);
    expect(labels).toEqual(["Allow once", "Deny"]);
    fireEvent.click([...loose.querySelectorAll("button")].find((b) => b.textContent === "Allow once")!);
    await waitFor(() => expect(api.terminal_approve_command).toHaveBeenCalledWith("r3", "once"));

    // A chat's row opens that chat at its card.
    const scrolled: string[] = [];
    const stop = subscribePendingCard("c2", () => scrolled.push("c2"));
    fireEvent.click(section.querySelector('[data-waiting-item="cmd:r2"]')!);
    expect(opened).toEqual(["c2"]);
    expect(scrolled).toEqual(["c2"]);
    stop();

    // Answered in another window: the row and the count go.
    act(() => pushLocalAgentEvent({ type: "terminal_command_decided", request_id: "r2", conv_id: "c2" }));
    await flush();
    expect(trigger.textContent).toContain("1 waiting");
  });
});
