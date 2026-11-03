// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

const host = vi.hoisted(() => ({ api: {} as Record<string, ReturnType<typeof vi.fn>> }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => host.api, isRemote: () => false }));

import { TEAM_API_METHODS, handleBridgeRequest } from "./bridge";
import { BRIDGE_CHANNEL } from "./constants";

afterEach(() => vi.resetAllMocks());

const call = (pluginId: string, method: string, args: unknown[] = []) =>
  handleBridgeRequest(
    { pluginId, panelId: "main", tabId: `plugin:${pluginId}:main`, version: 1, iframe: null } as never,
    { channel: BRIDGE_CHANNEL, id: "1", method: "team.call", params: { method, args } } as never,
  );

it("the account page reaches every team method with the same arguments and gets the result unchanged", async () => {
  expect(TEAM_API_METHODS.size).toBe(24);
  for (const m of TEAM_API_METHODS) {
    const result = { ok: true, m };
    host.api[m] = vi.fn().mockResolvedValue(result);
    expect(await call("account", m, ["a", { k: 1 }, true])).toEqual({
      channel: BRIDGE_CHANNEL, id: "1", ok: true, result,
    });
    expect(host.api[m]).toHaveBeenCalledWith("a", { k: 1 }, true);
  }
});

it("live_team_join(account, space) goes straight through", async () => {
  host.api.live_team_join = vi.fn().mockResolvedValue({ ok: true, on: true });
  expect(await call("account", "live_team_join", ["acc", "space"])).toMatchObject({ ok: true, result: { on: true } });
  expect(host.api.live_team_join).toHaveBeenCalledWith("acc", "space");
});

it("another plugin cannot call any of them, and the account page cannot call other API methods", async () => {
  host.api.plugin_scope_set = vi.fn();
  host.api.plugin_cache_clear = vi.fn();
  expect(await call("imk", "plugin_scope_set", ["imk", "team"])).toMatchObject({ ok: false });
  expect(host.api.plugin_scope_set).not.toHaveBeenCalled();
  expect(await call("account", "plugin_cache_clear", ["x"])).toMatchObject({ ok: false });
  expect(host.api.plugin_cache_clear).not.toHaveBeenCalled();
});

it("an error from the API comes back as a failed reply", async () => {
  host.api.plugin_scope_sync = vi.fn().mockRejectedValue(new Error("boom"));
  expect(await call("account", "plugin_scope_sync", ["imk"])).toMatchObject({ ok: false, error: "boom" });
});
