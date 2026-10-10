// @vitest-environment jsdom
import { act, cleanup, render, renderHook } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { EditorTab } from "../types/panel";

const api = vi.hoisted(() => ({ terminal_list: vi.fn() }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../hooks/useAgentEventBus", () => ({ subscribeAgentEvents: () => () => {} }));
vi.mock("../contexts/TerminalsSettingsContext", () => ({ getTerminalsEnabled: () => true }));

import { useTerminalTabs } from "./useTerminalTabs";

const editor = {
  openTab: vi.fn(),
  closeTabInLayout: vi.fn(),
  openTabsRef: { current: [] },
  remapTabId: vi.fn(),
  setOpenTabs: vi.fn(),
};

const session = (id: string) => ({ session_id: id, shell: "pwsh", cwd: "C:/work", title: `Shell ${id}`, ws_url: `ws://127.0.0.1:1/${id}` });

beforeEach(() => {
  vi.useFakeTimers();
  api.terminal_list.mockResolvedValue({ sessions: [session("a"), session("b")] });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  api.terminal_list.mockReset();
});

it("does not re-render the chat view when the parked shells are unchanged", async () => {
  // The hook lives in the chat view; count renders of what the view draws below it.
  let childRenders = 0;
  let parked: EditorTab[] = [];
  function Child({ tabs }: { tabs: EditorTab[] }) {
    childRenders += 1;
    parked = tabs;
    return null;
  }
  function View() {
    const { parkedTabs } = useTerminalTabs(editor);
    return createElement(Child, { tabs: parkedTabs });
  }
  render(createElement(View));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(parked.map((t) => t.terminalSessionId)).toEqual(["a", "b"]);
  const first = parked;
  const rendersAfterFirst = childRenders;

  for (let i = 0; i < 5; i++) await act(async () => { await vi.advanceTimersByTimeAsync(4000); });

  expect(api.terminal_list.mock.calls.length).toBeGreaterThanOrEqual(6);
  expect(childRenders).toBe(rendersAfterFirst);
  expect(parked).toBe(first);
});

it("still picks up a shell that started or ended elsewhere", async () => {
  const { result } = renderHook(() => useTerminalTabs(editor));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });

  api.terminal_list.mockResolvedValue({ sessions: [session("b"), session("c")] });
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
  expect(result.current.parkedTabs.map((t) => t.terminalSessionId).sort()).toEqual(["b", "c"]);

  api.terminal_list.mockResolvedValue({ sessions: [session("b"), { ...session("c"), title: "Renamed" }] });
  await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
  expect(result.current.parkedTabs.find((t) => t.terminalSessionId === "c")?.name).toContain("Renamed");
});
