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
