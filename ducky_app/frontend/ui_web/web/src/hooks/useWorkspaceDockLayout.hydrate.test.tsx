// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WorkspaceDockProvider, useWorkspaceDock } from "../workspace/WorkspaceDockContext";
import { defaultDockSnapshot, dockStorageKey, type WorkspaceDockSnapshot } from "../workspace/workspaceDockStorage";

const bridge = vi.hoisted(() => {
  const state: { saved: Record<string, unknown> } = { saved: {} };
  const api = {
    get_listener_status: () => Promise.resolve({}),
    get_workspace_dock: vi.fn((id: string) => Promise.resolve(id === "main" ? state.saved : {})),
    save_workspace_dock: vi.fn((_payload: { window_id: string; snapshot: unknown }) => Promise.resolve()),
  };
  return { state, api };
});

vi.mock("./usePanelApi", () => ({ getApi: () => bridge.api }));
vi.mock("./onApiReady", () => ({
  onApiReady: (cb: (api: unknown) => void) => {
    cb(bridge.api);
    return () => {};
  },
}));

beforeEach(() => {
  localStorage.clear();
  bridge.api.get_workspace_dock.mockClear();
  bridge.api.save_workspace_dock.mockClear();
  bridge.state.saved = { ...defaultDockSnapshot(), rightRailEnabled: false, rightRailOpen: false, leftWidth: 300 };
});
afterEach(cleanup);

/** ChatSidebar does this in an effect on mount; child effects run before the provider's. */
function FocusOnMount() {
  const dock = useWorkspaceDock();
  useEffect(() => {
    dock.setFocusedPanel("left", "chats");
  }, []);
  return null;
}

it("a panel focused on mount does not save the defaults over the saved layout", async () => {
  render(
    <WorkspaceDockProvider windowId="main">
      <FocusOnMount />
    </WorkspaceDockProvider>,
  );
  // Let the AppData read settle and any debounced (250 ms) save fire.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 400));
  });

  const saved = bridge.api.save_workspace_dock.mock.calls.map(([payload]) => payload.snapshot as WorkspaceDockSnapshot);
  expect(saved.filter((s) => s.rightRailEnabled !== false || s.leftWidth !== 300)).toEqual([]);
  const local = JSON.parse(localStorage.getItem(dockStorageKey("main")) || "{}") as WorkspaceDockSnapshot;
  expect(local.rightRailEnabled).toBe(false);
  expect(local.leftWidth).toBe(300);
});
