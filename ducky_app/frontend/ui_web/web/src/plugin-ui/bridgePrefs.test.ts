// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

const host = vi.hoisted(() => ({
  disk: {} as Record<string, Record<string, unknown>>,
  api: { plugin_prefs_get_all: vi.fn(), plugin_prefs_set: vi.fn() },
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => host.api, isRemote: () => false }));

import { handleBridgeRequest } from "./bridge";
import { BRIDGE_CHANNEL } from "./constants";

afterEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
});

const ctx = { pluginId: "browser", panelId: "browser", tabId: "plugin:browser:browser", version: "1", iframe: null } as never;
const call = (method: string, params: Record<string, unknown>) =>
  handleBridgeRequest(ctx, { channel: BRIDGE_CHANNEL, id: "1", method, params } as never);

it("a page's save keeps the keys Ducky wrote for the plugin (the Browser's allowed sites)", async () => {
  host.disk = { browser: { agentSites: "x.com", homepage: "https://a.example" } };
  host.api.plugin_prefs_get_all.mockImplementation(async () => ({ ok: true, prefs: host.disk }));
  host.api.plugin_prefs_set.mockImplementation(async (pid: string, slot: Record<string, unknown>) => {
    host.disk = { ...host.disk, [pid]: slot };
    return { ok: true };
  });
  await call("prefs.set", { id: "lastUrl", value: "https://x.com/home" });
  await call("prefs.set", { id: "homepage", value: null });
  await vi.waitFor(() => expect(host.api.plugin_prefs_set).toHaveBeenCalledTimes(2));
  expect(host.disk.browser).toEqual({ agentSites: "x.com", lastUrl: "https://x.com/home" });
  expect(await call("prefs.get", { id: "agentSites" })).toMatchObject({ ok: true, result: { value: "x.com" } });
});
