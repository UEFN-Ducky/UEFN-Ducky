// @vitest-environment jsdom
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const host = vi.hoisted(() => ({
  api: {
    browser_pane_open: vi.fn(async () => ({ ok: true })),
    browser_pane_close: vi.fn(async () => ({ ok: true })),
    browser_pane_set_bounds: vi.fn(async () => ({ ok: true })),
    browser_pane_list: vi.fn(async () => ({ panes: [] })),
  },
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => host.api, isRemote: () => false }));
vi.mock("../hooks/usePluginContributions", () => ({ usePluginContributions: () => ({ ui_panels: [] }) }));
vi.mock("../hooks/usePanelPushBus", () => ({ installPanelPushBus: () => {}, subscribePanelPush: () => () => {} }));
vi.mock("./usePluginPage", () => ({ usePluginPage: () => ({ src: "", linked: false }) }));
vi.mock("./pluginTheme", () => ({ usePluginThemePush: () => {} }));
vi.mock("./pluginReload", () => ({ reloadIframe: () => {}, useReloadOnPluginUpdate: () => {} }));
vi.mock("./PluginScopeBar", () => ({ PluginScopeBar: () => null }));
vi.mock("./DucktactoeChatPopup", () => ({ DucktactoeChatPopup: () => null }));
vi.mock("./PluginChatDock", () => ({ PluginChatDock: () => null, useDock: () => null }));
vi.mock("./pluginChatDockStore", () => ({ dockSnapshot: () => ({}), dropDock: () => {}, registerChatLookup: () => {} }));
vi.mock("../tabs/tabRegistryClient", () => ({ WINDOW_ID: "w" }));

import { PluginWebviewPane } from "./PluginWebviewPane";
import { handleBridgeRequest } from "./bridge";
import { BRIDGE_CHANNEL } from "./constants";

const TAB = "plugin:citybuilder:main";
let frames = new Map<number, FrameRequestCallback>();
let nextId = 0;
const raf = vi.fn((cb: FrameRequestCallback) => {
  frames.set(++nextId, cb);
  return nextId;
});

beforeEach(() => {
  frames = new Map();
  raf.mockClear();
  vi.stubGlobal("requestAnimationFrame", raf);
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
});
afterEach(() => vi.unstubAllGlobals());

const call = (method: string) =>
  handleBridgeRequest(
    { pluginId: "citybuilder", panelId: "main", tabId: TAB, iframe: null } as never,
    { channel: BRIDGE_CHANNEL, id: "1", method, params: {} } as never,
  );

const runFrames = () => {
  const due = [...frames.values()];
  frames.clear();
  for (const cb of due) cb(0);
};

it("a panel that never opens a browser pane requests no frames", () => {
  const view = render(<PluginWebviewPane tabId={TAB} />);
  expect(raf).not.toHaveBeenCalled();
  view.unmount();
});

it("frames start after browser.open and stop after browser.close", async () => {
  const view = render(<PluginWebviewPane tabId={TAB} />);
  expect(raf).not.toHaveBeenCalled();

  await act(async () => { await call("browser.open"); });
  expect(raf).toHaveBeenCalledTimes(1);
  runFrames();
  expect(raf).toHaveBeenCalledTimes(2);

  await act(async () => { await call("browser.close"); });
  raf.mockClear();
  expect(frames.size).toBe(0); // the queued frame was cancelled
  runFrames();
  expect(raf).not.toHaveBeenCalled();
  view.unmount();
});

it("a hidden tab runs no loop even with a pane", async () => {
  const view = render(<PluginWebviewPane tabId={TAB} visible={false} />);
  await act(async () => { await call("browser.open"); });
  expect(raf).not.toHaveBeenCalled();
  await act(async () => { await call("browser.close"); });
  view.unmount();
});
