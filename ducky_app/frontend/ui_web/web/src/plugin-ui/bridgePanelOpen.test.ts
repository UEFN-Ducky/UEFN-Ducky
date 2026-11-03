// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ openTab: vi.fn(), openSettings: vi.fn() }));
vi.mock("./openPluginUiTab", () => ({ requestOpenPluginUiTab: nav.openTab }));
vi.mock("../navigation/openSettingsTab", () => ({ requestOpenSettings: nav.openSettings }));

import { handleBridgeRequest } from "./bridge";
import { BRIDGE_CHANNEL } from "./constants";
import { panelItemKey, takePanelItem } from "./panelOpenItem";

afterEach(() => vi.resetAllMocks());

const menu = () => ({ badge: vi.fn(), close: vi.fn(), size: vi.fn() });
const call = (method: string, params: Record<string, unknown>, headerMenu?: ReturnType<typeof menu>) =>
  handleBridgeRequest({ pluginId: "Account", panelId: "account-team-menu", headerMenu } as never,
    { channel: BRIDGE_CHANNEL, id: "1", method, params } as never);

it("panel.open opens the calling plugin's own panel and closes the header menu", async () => {
  const host = menu();
  expect(await call("panel.open", { action: "panel:account-team" }, host)).toMatchObject({ ok: true });
  expect(nav.openTab).toHaveBeenCalledWith("account", "account-team");
  expect(host.close).toHaveBeenCalled();
});

it("panel.open leaves the item for the panel (Team tab on a section)", async () => {
  await call("panel.open", { action: "panel:account-team", item: "changes" });
  expect(takePanelItem(panelItemKey("account", "account-team"))).toBe("changes");
});

it("panel.open opens a Settings tab", async () => {
  expect(await call("panel.open", { action: "settings:Account" })).toMatchObject({ ok: true });
  expect(nav.openSettings).toHaveBeenCalledWith("Account");
});

it("panel.open refuses another plugin's panel and unknown actions", async () => {
  expect(await call("panel.open", { action: "panel:kanban/board" })).toMatchObject({ ok: false });
  expect(await call("panel.open", { action: "chat:123" })).toMatchObject({ ok: false });
  expect(await call("panel.open", { action: "panel:account-team", item: "x".repeat(65) })).toMatchObject({ ok: false });
  expect(nav.openTab).not.toHaveBeenCalled();
});

it("header_menu_size passes the page height to its menu and ignores garbage", async () => {
  const host = menu();
  expect(await call("header_menu_size", { height: 312.4 }, host)).toMatchObject({ ok: true });
  expect(host.size).toHaveBeenCalledWith(313);
  await call("header_menu_size", { height: "tall" }, host);
  await call("header_menu_size", { height: -5 }, host);
  expect(host.size).toHaveBeenCalledTimes(1);
  // Not a header menu page: refused.
  expect(await call("header_menu_size", { height: 200 })).toMatchObject({ ok: false });
});
