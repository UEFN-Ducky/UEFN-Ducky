// @vitest-environment jsdom
import { act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { _resetAskUserForTests, listAskUserSessions, runAskUser, settleAskUser } from "../ask-user/runAskUser";
import { _resetFocusedChatForAsk } from "../ask-user/focusedChatForAsk";
import { registerOpenChatReference } from "../navigation/openChatReference";
import { subscribePendingCard } from "../navigation/pendingCard";
import { _resetTerminalApprovalsForTests } from "../terminal/terminalApprovals";
import { pushLocalAgentEvent } from "./useAgentEventBus";
import {
  _resetWaitingChatsForTests,
  isChatWaiting,
  listWaitingItems,
  openWaitingChat,
  subscribeWaitingChats,
  waitingChatIds,
} from "./waitingChats";

const api = vi.hoisted(() => ({ terminal_pending_commands: vi.fn(async () => []) }));
vi.mock("./usePanelApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./usePanelApi")>()),
  getApi: () => api,
}));

const question = [{ id: "q", prompt: "Which map?", options: [{ id: "a", label: "A" }] }];
const command = (request_id: string, conv_id: string, cmd = "npm test") => ({
  type: "terminal_command_pending" as const,
  request_id,
  session_id: "s1",
  command: cmd,
  conv_id,
});

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
}

let changes = 0;
let stop: () => void = () => {};

beforeEach(() => {
  changes = 0;
  stop = subscribeWaitingChats(() => { changes += 1; });
});

afterEach(() => {
  stop();
  _resetAskUserForTests();
  _resetTerminalApprovalsForTests();
  _resetWaitingChatsForTests();
  _resetFocusedChatForAsk();
});

describe("chats waiting on the user", () => {
  it("knows several chats at once and clears each one as soon as its card is answered", async () => {
    void runAskUser(question, "", "c1");
    act(() => {
      pushLocalAgentEvent(command("r2", "c2"));
      pushLocalAgentEvent(command("r3", "c3", "npm run build"));
    });
    await flush();
    expect([...waitingChatIds()].sort()).toEqual(["c1", "c2", "c3"]);
    expect(listWaitingItems().map((item) => [item.kind, item.convId, item.text])).toEqual([
      ["question", "c1", "Which map?"],
      ["command", "c2", "npm test"],
      ["command", "c3", "npm run build"],
    ]);

    // Answered in this window.
    const ask = listAskUserSessions()[0];
    act(() => settleAskUser({ ok: true, answers: { q: { selected: ["a"], text: "", skipped: false } } }, ask.id));
    expect(isChatWaiting("c1")).toBe(false);

    // Answered in another window: the backend's decided event.
    act(() => pushLocalAgentEvent({ type: "terminal_command_decided", request_id: "r2", conv_id: "c2" }));
    await flush();
    expect(isChatWaiting("c2")).toBe(false);
    expect(isChatWaiting("c3")).toBe(true);
    expect(changes).toBeGreaterThanOrEqual(3);
  });

  it("marks the group hub that shows a member's question too", () => {
    void runAskUser(question, "", "member", { groupIds: ["hub"] });
    expect(isChatWaiting("member")).toBe(true);
    expect(isChatWaiting("hub")).toBe(true);
  });

  it("lists a question no chat owns without marking any chat", () => {
    void runAskUser(question, "", "");
    expect(listWaitingItems()).toHaveLength(1);
    expect(listWaitingItems()[0].convId).toBe("");
    expect(waitingChatIds().size).toBe(0);
  });

  it("opens the chat and asks it to scroll to the card", () => {
    const opened: string[] = [];
    const unregister = registerOpenChatReference((chat) => opened.push(chat.id));
    const scrolled: string[] = [];
    const unsubscribe = subscribePendingCard("c9", () => scrolled.push("c9"));
    try {
      expect(openWaitingChat("c9", "Builder")).toBe(true);
      expect(opened).toEqual(["c9"]);
      expect(scrolled).toEqual(["c9"]);
    } finally {
      unsubscribe();
      unregister();
    }
  });
});
