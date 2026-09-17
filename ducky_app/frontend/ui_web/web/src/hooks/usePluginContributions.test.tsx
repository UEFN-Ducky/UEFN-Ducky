// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const mock = vi.hoisted(() => ({
  get: vi.fn(),
  push: undefined as undefined | ((event: { type: string }) => void),
}));
vi.mock("./usePanelApi", () => ({ getApi: () => ({ get_uefn_plugin_contributions: mock.get }) }));
vi.mock("./onApiReady", () => ({ onApiReady: (fn: () => void) => { fn(); return () => {}; } }));
vi.mock("./usePanelPushBus", () => ({
  installPanelPushBus: () => {},
  subscribePanelPush: (fn: typeof mock.push) => { mock.push = fn; return () => {}; },
}));
import { usePluginContributions } from "./usePluginContributions";
afterEach(cleanup);

it("refreshes unchanged tabs when Store updates their UI or registers a panel later", async () => {
  const tab = { id: "Account", plugin_id: "account", label: "Ducky Account", ui: "builtin:account-settings" };
  const base = { ok: true, settings_tabs: [tab], ui_panels: [] as object[], enabled_ids: ["account"] };
  mock.get.mockResolvedValue(base);
  const { result } = renderHook(usePluginContributions);
  await waitFor(() => expect(result.current.ready).toBe(true));

  const updated = { ...base, settings_tabs: [{ ...tab, ui: "panel:account-settings" }] };
  mock.get.mockResolvedValue(updated);
  await act(async () => { mock.push?.({ type: "uefn_plugins_changed" }); });
  expect(result.current.settings_tabs[0].ui).toBe("panel:account-settings");

  const panel = { id: "account-settings", plugin_id: "account", entry: "ui/index.html", version: 18 };
  mock.get.mockResolvedValue({ ...updated, ui_panels: [panel] });
  await act(async () => { mock.push?.({ type: "uefn_plugins_changed" }); });
  expect(result.current.ui_panels).toEqual([panel]);

  mock.get.mockResolvedValue({ ...updated, ui_panels: [{ ...panel, version: 19 }] });
  await act(async () => { mock.push?.({ type: "uefn_plugins_changed" }); });
  expect(result.current.ui_panels[0].version).toBe(19);
  const unchanged = result.current;
  await act(async () => { mock.push?.({ type: "uefn_plugins_changed" }); });
  expect(result.current).toBe(unchanged);
});
