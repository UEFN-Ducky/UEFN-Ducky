// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

const host = vi.hoisted(() => ({
  api: { get_models_catalog: vi.fn(), pick_workflow_folder: vi.fn() },
  confirm: vi.fn(),
  alert: vi.fn(),
  pick: vi.fn(),
  rec: { start: vi.fn(), stop: vi.fn(), cancel: vi.fn() },
}));
vi.mock("./PluginModelPickerHost", () => ({ pickModelFromHost: host.pick }));
vi.mock("./hostRecorder", () => ({ startRecording: host.rec.start, stopRecording: host.rec.stop, cancelRecording: host.rec.cancel }));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => host.api, isRemote: () => false }));
vi.mock("../contexts/ConfirmModalContext", () => ({ confirmFromHost: host.confirm, alertFromHost: host.alert }));

import { handleBridgeRequest, shouldForwardPluginPush } from "./bridge";
import { BRIDGE_CHANNEL } from "./constants";

afterEach(() => {
  vi.resetAllMocks();
});

const ctx = { pluginId: "island-media-kit", panelId: "main", tabId: "plugin:imk:main", version: "1", iframe: null } as never;
const call = (method: string, params: Record<string, unknown>) =>
  handleBridgeRequest(ctx, { channel: BRIDGE_CHANNEL, id: "1", method, params } as never);

it("opens Ducky's own confirm modal for a plugin's question and returns the answer", async () => {
  host.confirm.mockResolvedValue(true);
  const res = await call("ui.confirm", { message: "Remove variant B?", danger: true });
  expect(host.confirm).toHaveBeenCalledWith(expect.objectContaining({ message: "Remove variant B?", danger: true }));
  expect(res).toMatchObject({ ok: true, result: { confirmed: true } });
});

it("treats a cancelled or extra-button answer as not confirmed", async () => {
  host.confirm.mockResolvedValue("extra");
  expect(await call("ui.confirm", { message: "Delete?" })).toMatchObject({ ok: true, result: { confirmed: false } });
});

it("refuses an empty question", async () => {
  expect(await call("ui.confirm", { message: "  " })).toMatchObject({ ok: false });
  expect(host.confirm).not.toHaveBeenCalled();
});

it("lists the chat models a plugin can pass to llm.complete, without keys or prices", async () => {
  host.api.get_models_catalog.mockResolvedValue({
    default_model: "m-default",
    agent_model: "m-agent",
    models: [{ id: "m1", name: "Model One", provider: "openai", price_in: 3, is_local: false, supports_vision: true }],
  });
  const res = await call("models.list", {});
  expect(res).toMatchObject({ ok: true, result: { default_model: "m-agent", models: [{ id: "m1", name: "Model One", provider: "openai", is_local: false, supports_vision: true }] } });
  expect(JSON.stringify(res)).not.toContain("price_in");
});

it("opens the Windows folder picker and returns the chosen folder", async () => {
  host.api.pick_workflow_folder.mockResolvedValue({ ok: true, folder: "D:/Trailers" });
  expect(await call("files.pickFolder", {})).toMatchObject({ ok: true, result: { folder: "D:/Trailers" } });
  host.api.pick_workflow_folder.mockResolvedValue({ ok: true, folder: "" });
  expect(await call("files.pickFolder", {})).toMatchObject({ ok: true, result: { folder: "" } });
});

it("opens Ducky's model picker for a panel and returns the qualified pick", async () => {
  host.pick.mockResolvedValue({ model: "openai:m1", name: "Model One" });
  const res = await call("ui.pickModel", { purpose: "video", current: "" });
  expect(host.pick).toHaveBeenCalledWith(expect.objectContaining({ purpose: "video" }));
  expect(res).toMatchObject({ ok: true, result: { model: "openai:m1", name: "Model One" } });
  host.pick.mockResolvedValue(null);
  expect(await call("ui.pickModel", {})).toMatchObject({ ok: true, result: { model: "" } });
});

it("passes a panel's extra picker rows through to the host", async () => {
  host.pick.mockResolvedValue({ model: "typesafe:jev-1.14.0", name: "Jev 1.14.0" });
  const extra = [{ id: "typesafe:jev-1.14.0", name: "Jev 1.14.0", group: "TypeSafe", note: "Not for chat" }];
  const res = await call("ui.pickModel", { purpose: "video", extra, hide: ["uefn_ducky"] });
  expect(host.pick).toHaveBeenCalledWith(expect.objectContaining({ extra, hide: ["uefn_ducky"] }));
  expect(res).toMatchObject({ ok: true, result: { model: "typesafe:jev-1.14.0" } });
});

it("moves a panel's anchor box onto the app page so the picker drops down from the button", async () => {
  host.pick.mockResolvedValue(null);
  const iframe = { getBoundingClientRect: () => ({ left: 300, top: 40 }) } as unknown as HTMLIFrameElement;
  const anchored = { ...(ctx as object), iframe } as never;
  await handleBridgeRequest(anchored, {
    channel: BRIDGE_CHANNEL, id: "2", method: "ui.pickModel",
    params: { purpose: "video", anchor: { left: 10, top: 5, width: 120, height: 28 } },
  } as never);
  expect(host.pick).toHaveBeenCalledWith(expect.objectContaining({ anchor: { left: 310, top: 45, width: 120, height: 28 } }));
});

it("records from the microphone for a panel, scoped to that plugin", async () => {
  host.rec.start.mockResolvedValue({ id: "rec-1" });
  host.rec.stop.mockResolvedValue({ b64: "QUJD", mime: "audio/webm", seconds: 2.5, bytes: 3 });
  expect(await call("audio.recordStart", {})).toMatchObject({ ok: true, result: { id: "rec-1" } });
  expect(host.rec.start).toHaveBeenCalledWith("island-media-kit");
  expect(await call("audio.recordStop", { id: "rec-1" })).toMatchObject({ ok: true, result: { mime: "audio/webm" } });
  expect(host.rec.stop).toHaveBeenCalledWith("island-media-kit", "rec-1");
});

it("never forwards the app's raw plugin_scope_changed push: the scope bar tells panels itself", async () => {
  expect(await call("plugin.subscribe", { types: ["imk_project_changed", "plugin_scope_changed"] })).toMatchObject({ ok: true });
  expect(shouldForwardPluginPush("plugin:imk:main", "island-media-kit", "imk_project_changed")).toBe(true);
  expect(shouldForwardPluginPush("plugin:imk:main", "island-media-kit", "plugin_scope_changed")).toBe(false);
  await call("plugin.subscribe", { types: [] }); // every type
  expect(shouldForwardPluginPush("plugin:imk:main", "island-media-kit", "discord_message")).toBe(true);
  expect(shouldForwardPluginPush("plugin:imk:main", "island-media-kit", "plugin_scope_changed")).toBe(false);
});
