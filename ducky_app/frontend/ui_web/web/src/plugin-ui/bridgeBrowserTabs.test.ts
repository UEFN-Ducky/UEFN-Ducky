// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

const host = vi.hoisted(() => ({
  api: { browser_pane_open: vi.fn(), browser_pane_list: vi.fn(), browser_pane_close: vi.fn() },
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => host.api, isRemote: () => false }));

import { handleBridgeRequest, ownsBrowserPane, paneCoveredByHtml } from "./bridge";
import { BRIDGE_CHANNEL } from "./constants";

afterEach(() => {
  vi.resetAllMocks();
  document.body.innerHTML = "";
});

const TAB = "plugin:browser:browser";
const ctx = { pluginId: "browser", panelId: "browser", tabId: TAB, iframe: null } as never;
const call = (method: string, params: Record<string, unknown>) =>
  handleBridgeRequest(ctx, { channel: BRIDGE_CHANNEL, id: "1", method, params } as never);

it("page tabs are panes under the Browser tab, and a bad tab id can't reach another pane", async () => {
  host.api.browser_pane_open.mockResolvedValue({ ok: true });
  await call("browser.open", { tab: "t1", url: "https://x.com" });
  expect(host.api.browser_pane_open).toHaveBeenLastCalledWith(`${TAB}::t1`, "https://x.com", "");
  await call("browser.open", { tab: "../plugin:discord", url: "" });
  expect(host.api.browser_pane_open).toHaveBeenLastCalledWith(TAB, "", "");
  expect(ownsBrowserPane(TAB, `${TAB}::t1`)).toBe(true);
  expect(ownsBrowserPane(TAB, `${TAB}2::t1`)).toBe(false);
});

it("browser.tabs lists only this Browser tab's page tabs", async () => {
  host.api.browser_pane_list.mockResolvedValue({
    panes: [{ pane_id: `${TAB}::t1` }, { pane_id: "plugin:other:x::t2" }, { pane_id: `${TAB}::t3` }],
  });
  const res = (await call("browser.tabs", {})) as { result: { panes: { pane_id: string }[] } };
  expect(res.result.panes.map((p) => p.pane_id)).toEqual([`${TAB}::t1`, `${TAB}::t3`]);
});

it("Ducky's own dialogs, dim layers and tooltips over the page count as covering it", () => {
  const iframe = document.createElement("iframe");
  document.body.appendChild(iframe);
  const hitFrame = vi.fn(() => iframe as Element);
  (document as { elementFromPoint?: unknown }).elementFromPoint = hitFrame;
  expect(paneCoveredByHtml(iframe, 0, 0, 400, 300)).toBe(false);

  const backdrop = document.createElement("div");
  (document as { elementFromPoint?: unknown }).elementFromPoint = () => backdrop;
  expect(paneCoveredByHtml(iframe, 0, 0, 400, 300)).toBe(true);

  (document as { elementFromPoint?: unknown }).elementFromPoint = hitFrame;
  const tip = document.createElement("div");
  tip.setAttribute("role", "tooltip");
  tip.getBoundingClientRect = () => ({ left: 10, top: -20, right: 200, bottom: 20, width: 190, height: 40 }) as DOMRect;
  document.body.appendChild(tip);
  expect(paneCoveredByHtml(iframe, 0, 0, 400, 300)).toBe(true);
  tip.getBoundingClientRect = () => ({ left: 10, top: -60, right: 200, bottom: -5, width: 190, height: 55 }) as DOMRect;
  expect(paneCoveredByHtml(iframe, 0, 0, 400, 300)).toBe(false);
});

it("a popup faded out through its parent never counts as covering the page", () => {
  // Nov 1 2026: an agent's localhost page stayed blank in the Browser column beside the chat.
  const iframe = document.createElement("iframe");
  document.body.appendChild(iframe);
  (document as { elementFromPoint?: unknown }).elementFromPoint = () => iframe;
  const tip = document.createElement("div");
  tip.setAttribute("role", "tooltip");
  tip.getBoundingClientRect = () => ({ left: 10, top: 10, right: 200, bottom: 60, width: 190, height: 50 }) as DOMRect;
  document.body.appendChild(tip);
  (tip as unknown as { checkVisibility: () => boolean }).checkVisibility = () => false;
  expect(paneCoveredByHtml(iframe, 0, 0, 400, 300)).toBe(false);

  const dim = document.createElement("div");
  (dim as unknown as { checkVisibility: () => boolean }).checkVisibility = () => false;
  (document as { elementFromPoint?: unknown }).elementFromPoint = () => dim;
  expect(paneCoveredByHtml(iframe, 0, 0, 400, 300)).toBe(false);
  (dim as unknown as { checkVisibility: () => boolean }).checkVisibility = () => true;
  expect(paneCoveredByHtml(iframe, 0, 0, 400, 300)).toBe(true);
});
