// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, expect, it, vi } from "vitest";
import type { EditorTab } from "../types/panel";

const plan = vi.hoisted(() => ({ imported: 0 }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => null }));
vi.mock("../hooks/useAgentEventBus", () => ({ useAgentEventSubscription: () => {}, subscribeAgentEvents: () => () => {} }));
vi.mock("./ChatPane", () => ({ ChatPane: () => null }));
vi.mock("./EditorTabs", () => ({ EditorTabs: () => null }));
vi.mock("./MobileTabSwitcher", () => ({ MobileTabSwitcher: () => null, useHeaderTabsSlot: () => null }));
vi.mock("../hooks/useNarrowLayout", () => ({ useNarrowLayout: () => false }));
vi.mock("../contexts/TerminalsSettingsContext", () => ({ useTerminalsSettings: () => ({ enabled: false }) }));
vi.mock("./FileEditorPane", () => ({ FileEditorPane: () => <div>File</div> }));
// The factory runs when the module is first imported: that is the lazy load.
vi.mock("./PlanPane", () => {
  plan.imported += 1;
  return { PlanPane: ({ chatName }: { chatName: string }) => <div>Plan {chatName}</div> };
});
vi.mock("./usage/ProviderUsageReport", () => ({ ProviderUsageReport: () => null }));
vi.mock("../plugin-ui", () => ({ PluginWebviewPane: () => null }));
vi.mock("../plugin-ui/DucktactoeChatShell", () => ({ DucktactoeChatShell: () => null }));
vi.mock("./VerseTranslatedPane", () => ({ VerseTranslatedPane: () => null }));
vi.mock("./ducky/DuckyProfileTabPane", () => ({ DuckyProfileTabPane: () => null }));

import { EditorGroupPane } from "./EditorGroupPane";

const tabs: EditorTab[] = [
  { id: "file", kind: "file", name: "a.verse", path: "a.verse" },
  { id: "plan-x", kind: "plan", chatId: "x", name: "Build plan" },
];
function props(activeTabId: string): ComponentProps<typeof EditorGroupPane> {
  return {
    group: { id: "main", tabIds: tabs.map((t) => t.id), activeTabId },
    openTabs: tabs, isFocused: true, allChats: [], runningChatIds: new Set(),
    onOpenChat: vi.fn(), onFocusGroup: vi.fn(), onActivateTab: vi.fn(),
    onCloseTab: vi.fn(), onReorderTabs: vi.fn(), onDropTab: vi.fn(),
  };
}
afterEach(cleanup);

it("the plan pane is not imported until a plan tab shows, then renders", async () => {
  const view = render(<EditorGroupPane {...props("file")} />);
  expect(view.getByText("File")).toBeTruthy();
  expect(plan.imported).toBe(0);
  view.rerender(<EditorGroupPane {...props("plan-x")} />);
  expect(await view.findByText("Plan Build plan")).toBeTruthy();
  expect(plan.imported).toBe(1);
});
