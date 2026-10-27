// @vitest-environment jsdom
import { expect, it, vi } from "vitest";

const link = vi.hoisted(() => ({ asked: [] as string[] }));
vi.mock("../remote/directTransport", () => ({
  getDirectTransport: () => ({
    fetchBlob: async (path: string) => {
      link.asked.push(path);
      return new Response("hi", { status: 200, headers: { "Content-Type": "application/json" } });
    },
  }),
}));

import { handleBridgeRequest } from "./bridge";
import { BRIDGE_CHANNEL } from "./constants";

const ctx = { pluginId: "googledrive", panelId: "main", iframe: null } as never;
const call = (path: string) =>
  handleBridgeRequest(ctx, { channel: BRIDGE_CHANNEL, id: "1", method: "desktop.file", params: { path } } as never);

it("a phone-built page reads its own files and the kit's from the desktop, nothing else", async () => {
  const own = await call("/plugin-ui/googledrive/ui/data.json?v=2");
  expect(own.ok).toBe(true);
  expect(own.result).toMatchObject({ status: 200, type: "application/json" });
  expect(new TextDecoder().decode((own.result as { body: ArrayBuffer }).body)).toBe("hi");
  expect((await call("/plugin-ui/_kit/ducky.css")).ok).toBe(true);
  for (const bad of [
    "/plugin-ui/account/ui/index.html",
    "/plugin-ui/googledrive/../account/ui/index.html",
    "/plugin-ui/googledrive/%2e%2e/account/x",
    "/__panel_api/list_chats",
  ]) {
    expect((await call(bad)).ok, bad).toBe(false);
  }
  expect(link.asked).toEqual(["/plugin-ui/googledrive/ui/data.json?v=2", "/plugin-ui/_kit/ducky.css"]);
});
