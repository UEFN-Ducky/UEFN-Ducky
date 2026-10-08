// @vitest-environment jsdom
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { useState, type ComponentProps } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ChatTab, EditorTab } from "../types/panel";
import { useChatMessages } from "../hooks/useChatMessages";

const api = vi.hoisted(() => ({ load_messages: vi.fn().mockResolvedValue([]) }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../hooks/useAgentEventBus", () => ({ useAgentEventSubscription: () => {}, subscribeAgentEvents: () => () => {} }));
vi.mock("./ChatPane", () => ({
  ChatPane: ({ chat, visible }: { chat: ChatTab; visible: boolean }) => {
    const [draft, setDraft] = useState("");
    const { hydrated } = useChatMessages(chat.id, visible, false);
    return <section data-testid={chat.id} data-visible={visible}>
      {!hydrated && <span>Loading</span>}
      <input aria-label={chat.id} value={draft} onChange={(e) => setDraft(e.target.value)} />
    </section>;
  },
}));
vi.mock("./EditorTabs", () => ({ EditorTabs: () => null }));
vi.mock("./MobileTabSwitcher", () => ({ MobileTabSwitcher: () => null, useHeaderTabsSlot: () => null }));
vi.mock("../hooks/useNarrowLayout", () => ({ useNarrowLayout: () => false }));
vi.mock("../contexts/TerminalsSettingsContext", () => ({ useTerminalsSettings: () => ({ enabled: false }) }));
vi.mock("./FileEditorPane", () => ({ FileEditorPane: () => <div>File</div> }));
vi.mock("./PlanPane", () => ({ PlanPane: () => null }));
vi.mock("./usage/ProviderUsageReport", () => ({ ProviderUsageReport: () => null }));
vi.mock("../terminal/TerminalPane", () => ({ TerminalPane: () => null }));
vi.mock("../views/SettingsView", () => ({ SettingsView: () => <div>Settings</div> }));
vi.mock("../plugin-ui", () => ({ PluginWebviewPane: () => null }));
vi.mock("../plugin-ui/DucktactoeChatShell", () => ({ DucktactoeChatShell: () => null }));
vi.mock("./VerseTranslatedPane", () => ({ VerseTranslatedPane: () => null }));
vi.mock("./ducky/DuckyProfileTabPane", () => ({ DuckyProfileTabPane: () => null }));

import { EditorGroupPane } from "./EditorGroupPane";

const tabs: EditorTab[] = [
  { id: "chat-a", chatId: "a", kind: "chat", name: "Same name" },
  { id: "chat-b", chatId: "b", kind: "chat", name: "Same name" },
  { id: "chat-c", chatId: "c", kind: "chat", name: "Unvisited" },
  { id: "settings", kind: "settings", name: "Settings" },
];
function props(activeTabId: string, openTabs = tabs): ComponentProps<typeof EditorGroupPane> {
  return {
    group: { id: "main", tabIds: openTabs.map((t) => t.id), activeTabId },
    openTabs, isFocused: true, allChats: [], runningChatIds: new Set(),
    onOpenChat: vi.fn(), onFocusGroup: vi.fn(), onActivateTab: vi.fn(),
    onCloseTab: vi.fn(), onReorderTabs: vi.fn(), onDropTab: vi.fn(),
  };
}
beforeEach(() => api.load_messages.mockClear());
afterEach(cleanup);

it("retains visited chats by ID, their draft and scroll DOM, without reloading or preloading unvisited tabs", async () => {
  const view = render(<EditorGroupPane {...props("chat-a")} />);
  await waitFor(() => expect(view.queryByText("Loading")).toBeNull());
  const paneA = view.getByTestId("a");
  const inputA = view.getByLabelText("a");
  paneA.scrollTop = 375;
  fireEvent.change(inputA, { target: { value: "Unsent draft" } });
  view.rerender(<EditorGroupPane {...props("chat-b")} />);
  await waitFor(() => expect(view.queryByText("Loading")).toBeNull());
  expect(paneA.parentElement?.hidden).toBe(true);
  expect(paneA.dataset.visible).toBe("false");
  expect(view.queryByTestId("c")).toBeNull();
  view.rerender(<EditorGroupPane {...props("settings")} />);
  expect(view.getByText("Settings")).toBeTruthy();
  view.rerender(<EditorGroupPane {...props("chat-a")} />);
  expect(view.getByTestId("a")).toBe(paneA);
  expect(paneA.parentElement?.hidden).toBe(false);
  expect(paneA.dataset.visible).toBe("true");
  expect(paneA.scrollTop).toBe(375);
  expect((view.getByLabelText("a") as HTMLInputElement).value).toBe("Unsent draft");
  expect(api.load_messages.mock.calls.map(([id]) => id)).toEqual(["a", "b"]);
});

it("releases closed chat panes and mounts a fresh pane when reopened", async () => {
  const view = render(<EditorGroupPane {...props("chat-a")} />);
  await waitFor(() => expect(view.queryByText("Loading")).toBeNull());
  const original = view.getByTestId("a");
  view.rerender(<EditorGroupPane {...props("settings", tabs.filter((t) => t.id !== "chat-a"))} />);
  expect(view.queryByTestId("a")).toBeNull();
  view.rerender(<EditorGroupPane {...props("chat-a")} />);
  expect(view.getByTestId("a")).not.toBe(original);
  await waitFor(() => expect(api.load_messages).toHaveBeenCalledTimes(2));
});
