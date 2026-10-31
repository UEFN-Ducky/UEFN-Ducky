// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useEditorLayout } from "./useEditorLayout";
import { collectTabIds } from "../utils/editorLayoutOps";
import type { EditorTab } from "../types/panel";

vi.mock("../sfx/appHooks", () => ({ emitAppHook: vi.fn() }));
afterEach(cleanup);
const tab = (id: string): EditorTab => ({ id: `chat:${id}`, kind: "chat", chatId: id, name: id });

it("keeps the group identity when reopening its only tab or closing its neighbor", () => {
  const { result } = renderHook(() => useEditorLayout([tab("a")]));
  const group = result.current.layout.focusedGroupId;
  act(() => result.current.openTab(tab("a")));
  expect(result.current.layout.focusedGroupId).toBe(group);
  act(() => result.current.openTab(tab("b")));
  act(() => result.current.closeTabInLayout("chat:b"));
  expect(result.current.layout.focusedGroupId).toBe(group);
});

it("keeps just the latest preview when opening multiple chats in one update", () => {
  const { result } = renderHook(() => useEditorLayout([tab("pinned")]));
  act(() => {
    result.current.openTab(tab("a"), { preview: true });
    result.current.openTab(tab("b"), { preview: true });
    result.current.openTab(tab("c"), { preview: true });
  });
  expect(result.current.openTabs.map((t) => t.id)).toEqual(["chat:pinned", "chat:c"]);
  expect(collectTabIds(result.current.layout)).toEqual(["chat:pinned", "chat:c"]);
});

it("does not reopen tabs when closing several tabs in one update", () => {
  const { result } = renderHook(() => useEditorLayout([tab("a"), tab("b"), tab("c")]));
  act(() => {
    result.current.closeTabInLayout("chat:a");
    result.current.closeTabInLayout("chat:b");
  });
  expect(result.current.openTabs.map((t) => t.id)).toEqual(["chat:c"]);
  expect(collectTabIds(result.current.layout)).toEqual(["chat:c"]);
});

it("opens an agent's plugin pages in a column right of the chat, stacked, without taking focus", () => {
  // Oct 31 2026: the person wants the agent's Browser beside the chat, a second one under it.
  const page = (id: string): EditorTab => ({ id: `plugin:${id}`, kind: "plugin", name: id });
  const { result } = renderHook(() => useEditorLayout([tab("a")]));
  const chatGroup = result.current.layout.focusedGroupId;
  const groupOf = (id: string) =>
    Object.values(result.current.layout.groups).find((g) => g.tabIds.includes(id))?.id;
  act(() => result.current.openTabInSideColumn(page("browser")));
  const root = result.current.layout.root;
  expect(root.type === "split" && root.axis).toBe("row");
  expect(result.current.layout.focusedGroupId).toBe(chatGroup);
  expect(groupOf("plugin:browser")).not.toBe(chatGroup);

  act(() => result.current.openTabInSideColumn(page("second")));
  const right = result.current.layout.root.type === "split" ? result.current.layout.root.children[1] : null;
  expect(right?.type === "split" && right.axis).toBe("column");
  expect(groupOf("plugin:second")).not.toBe(groupOf("plugin:browser"));

  act(() => result.current.openTabInSideColumn(page("third")));
  act(() => result.current.openTabInSideColumn(page("fourth")));
  expect(Object.keys(result.current.layout.groups)).toHaveLength(4);
  expect(groupOf("plugin:fourth")).toBe(groupOf("plugin:third"));

  const before = result.current.layout.groups[groupOf("plugin:browser")!];
  act(() => result.current.openTabInSideColumn(page("browser")));
  expect(Object.keys(result.current.layout.groups)).toHaveLength(4);
  expect(result.current.layout.groups[before.id].activeTabId).toBe("plugin:browser");
  expect(result.current.layout.focusedGroupId).toBe(chatGroup);
});

it("puts a page the person opens beside the chat and focuses it there", () => {
  // Oct 31 2026: Ledger, Workflows… opened from the header covered the chat.
  const page = (id: string, kind: EditorTab["kind"]): EditorTab => ({ id, kind, name: id });
  const { result } = renderHook(() => useEditorLayout([tab("a")]));
  const chatGroup = result.current.layout.focusedGroupId;
  act(() => result.current.openTabInSideColumn(page("changes", "changes"), { focus: true }));
  const ledgerGroup = Object.values(result.current.layout.groups).find((g) => g.tabIds.includes("changes"))!.id;
  expect(ledgerGroup).not.toBe(chatGroup);
  expect(result.current.layout.focusedGroupId).toBe(ledgerGroup);
  expect(result.current.layout.groups[chatGroup].activeTabId).toBe("chat:a");
  act(() => result.current.openTabInSideColumn(page("workflows", "workflows"), { focus: true }));
  const flowGroup = Object.values(result.current.layout.groups).find((g) => g.tabIds.includes("workflows"))!.id;
  expect(flowGroup).not.toBe(ledgerGroup);
  expect(result.current.layout.focusedGroupId).toBe(flowGroup);
});
