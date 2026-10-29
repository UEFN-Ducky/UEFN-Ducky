// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

const host = vi.hoisted(() => ({
  api: { create_conversation: vi.fn(async () => ({ id: "conv-art", title: "Art Studio AI" })) },
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => host.api, isRemote: () => false }));

import { handleBridgeRequest } from "./bridge";
import { BRIDGE_CHANNEL } from "./constants";
import { getDock } from "./pluginChatDockStore";

afterEach(() => localStorage.clear());

const ctx = { pluginId: "artstudio", panelId: "main", tabId: "plugin:artstudio:main", iframe: null } as never;
const call = (method: string, params: Record<string, unknown> = {}) =>
  handleBridgeRequest(ctx, { channel: BRIDGE_CHANNEL, id: "1", method, params } as never);

it("chat.open / set / state / close drive the pane's docked Ducky chat", async () => {
  expect(await call("chat.set", { expanded: true })).toMatchObject({ ok: false, error: "chat.set: call chat.open first" });
  const opened = await call("chat.open", { key: "doc1", title: "Art Studio AI", brief: "Use artstudio_* tools.", place: "bottom", theme: { "--dock-accent": "#c084fc" } });
  expect(opened).toMatchObject({ ok: true, result: { open: true, expanded: false, place: "bottom", size: 320, conv_id: "conv-art" } });
  expect(getDock("plugin:artstudio:main")?.theme).toEqual({ "--dock-accent": "#c084fc" });
  expect(await call("chat.set", { expanded: true, size: 260 })).toMatchObject({ result: { expanded: true, size: 260 } });
  expect(await call("chat.state")).toMatchObject({ result: { open: true, conv_id: "conv-art" } });
  expect(await call("chat.close")).toMatchObject({ result: { open: false } });
  expect(host.api.create_conversation).toHaveBeenCalledTimes(1);
});
