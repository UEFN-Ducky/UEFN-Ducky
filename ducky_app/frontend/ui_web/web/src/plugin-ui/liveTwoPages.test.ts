// @vitest-environment jsdom
/** Two panel pages on two PCs in one live group, each its own window running the live script
 * its app puts in the page (this app's, or 1.2.433's as shipped: ./fixtures), joined the way
 * the apps join them: what a page asks for goes through its app's bridge (this one's
 * handleBridgeRequest, or 1.2.433's, which sent a tab click as its anchor alone and a screen
 * without its name) and over the group's link to the other page. The plugin is a page with
 * Thumbnails / Video / Rules tabs, like Island Media Kit's. What is checked is what each person
 * sees: the tab their screen shows after someone clicks one. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { JSDOM } from "jsdom";
import { afterEach, expect, it, vi } from "vitest";

const link = vi.hoisted(() => ({ send: (..._args: unknown[]): unknown => undefined }));
vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({ plugin_live_send: (...args: unknown[]) => Promise.resolve(link.send(...args)) }),
  isRemote: () => false,
}));

import { handleBridgeRequest } from "./bridge";
import { BRIDGE_CHANNEL } from "./constants";

const TABS = ["thumbs", "video", "rules"];

function script(app: "now" | "1.2.433"): string {
  if (app === "1.2.433") return readFileSync(join(__dirname, "fixtures/livePresenceScript-1.2.433.js"), "utf8");
  const py = readFileSync(join(__dirname, "../../../../../backend/uefn_plugins/ui_kit.py"), "utf8");
  const open = 'LIVE_PRESENCE_SCRIPT = r"""';
  const start = py.indexOf(open) + open.length;
  return py.slice(start, py.indexOf('"""', start)).replace(/^<script>/, "").replace(/<\/script>$/, "");
}

type Request = { channel?: string; id?: string; method?: string; params?: Record<string, unknown> };
type Page = { name: string; device: string; app: "now" | "1.2.433"; win: JSDOM["window"]; out: Request[];
  clicks: Record<string, number>; shown: () => string; click: (tab: string) => Promise<void> };

const group: Page[] = [];

function tell(p: Page, event: Record<string, unknown>): void {
  p.win.dispatchEvent(new p.win.MessageEvent("message", { data: { channel: BRIDGE_CHANNEL, event }, source: p.win }));
}

/** The group's link: a message from ``from`` reaches the others (or the one ``to`` names) as the
 * app hands it to its panels. */
function deliver(from: Page, topic: string, data: unknown, to: string): void {
  for (const p of group) {
    if (p !== from && (!to || to === p.device))
      tell(p, { type: "live.message", from: { name: from.name, device: from.device }, topic, data: JSON.parse(JSON.stringify(data)) });
  }
}

link.send = (pluginId, topic, data, to) => {
  const from = group.find((p) => `imk@${p.device}` === pluginId);
  if (from) deliver(from, String(topic), data, String(to || ""));
  return { ok: true, sent: 1 };
};

/** 1.2.433's bridge (liveHost): a tab click as its anchor, a screen as its id and scroll, to everyone. */
function oldBridge(p: Page, method: string, params: Record<string, unknown>): void {
  if (method === "live.nav" && typeof params.anchor === "string") deliver(p, "ducky.nav", { anchor: params.anchor }, "");
  if (method === "live.view") deliver(p, "ducky.view", { v: String(params.v || ""), scroll: params.scroll || [] }, "");
}

/** Every page's requests go through its app until nobody has anything left to send. */
async function settle(): Promise<void> {
  for (let round = 0; round < 50; round++) {
    let moved = false;
    for (const p of group) {
      for (const req of p.out.splice(0)) {
        moved = true;
        if (req?.channel !== BRIDGE_CHANNEL || !String(req.method).startsWith("live.")) continue;
        if (p.app === "1.2.433") oldBridge(p, String(req.method), req.params || {});
        else await handleBridgeRequest({ pluginId: `imk@${p.device}`, panelId: "main", tabId: "plugin:imk:main", iframe: null } as never,
          req as never);
      }
    }
    if (!moved) return;
    await Promise.resolve();
  }
}

function page(name: string, device: string, app: "now" | "1.2.433"): Page {
  const tabs = TABS.map((t) => `<button role="tab" data-live-nav data-live-id="imk-tab-${t}" aria-selected="${t === "thumbs"}">${t}</button>`);
  const dom = new JSDOM(`<!doctype html><body data-live-view="p1:thumbs"><nav>${tabs.join("")}</nav></body>`,
    { runScripts: "outside-only", url: "https://imk.plugin.local/panel" });
  const win = dom.window;
  const doc = win.document;
  const show = (tab: string) => {
    for (const b of doc.querySelectorAll("[role=tab]")) b.setAttribute("aria-selected", String(b.getAttribute("data-live-id") === `imk-tab-${tab}`));
    doc.body.setAttribute("data-live-view", `p1:${tab}`);
  };
  const p: Page = {
    name, device, app, win, out: [], clicks: {},
    shown: () => (doc.body.getAttribute("data-live-view") || "").split(":")[1],
    click: async (tab: string) => {
      (doc.querySelector(`[data-live-id="imk-tab-${tab}"]`) as HTMLElement).click();
      await settle();
    },
  };
  for (const t of TABS) doc.querySelector(`[data-live-id="imk-tab-${t}"]`)?.addEventListener("click", () => {
    p.clicks[t] = (p.clicks[t] || 0) + 1;
    show(t);
  });
  win.addEventListener("ducky-live-goto", (e) => {  // the plugin opens the leader's screen, as Island Media Kit does
    const tab = String((e as CustomEvent<{ view: string }>).detail.view).split(":")[1];
    if (TABS.includes(tab)) show(tab);
  });
  (win as unknown as { postMessage: (m: Request) => void }).postMessage = (m) => p.out.push(m);  // the page asking its app
  win.eval(script(app));
  group.push(p);
  return p;
}

async function goLive(p: Page, follow: Record<string, unknown>): Promise<void> {
  tell(p, { type: "live.state", on: true });
  tell(p, { type: "live.follow", ...follow });
  await settle();
}

afterEach(() => {
  for (const p of group.splice(0)) p.win.close();
});

it("an older app hosts the group: the up-to-date PC leads it and does the older one's clicks; the older screen stays its own", async () => {
  // iliya's Laptop is on 1.2.433 and the room is theirs; tas's app is up to date, so it leads.
  const laptop = page("iliya", "laptop", "1.2.433");
  const tas = page("tas", "mainpc", "now");
  await goLive(laptop, { device: "" });  // 1.2.433 hosting: follows nobody
  await goLive(tas, { device: "", leading: true, older: ["laptop"] });
  await laptop.click("video");
  expect([laptop.shown(), tas.shown()]).toEqual(["video", "video"]);
  await tas.click("rules");
  expect([laptop.shown(), tas.shown()]).toEqual(["video", "rules"]);  // the Live menu says iliya needs to update
});

it("once both apps are up to date, clicks go both ways through the leader, each page doing each one once", async () => {
  const laptop = page("iliya", "laptop", "now");
  const tas = page("tas", "mainpc", "now");
  await goLive(laptop, { device: "", leading: true, older: [] });
  await goLive(tas, { device: "laptop", leading: false, older: [] });
  await tas.click("video");  // a member's click: it asks the leader, which does it and tells everyone
  expect([laptop.shown(), tas.shown()]).toEqual(["video", "video"]);
  expect([laptop.clicks.video, tas.clicks.video]).toEqual([1, 1]);
  await laptop.click("rules");
  expect([laptop.shown(), tas.shown()]).toEqual(["rules", "rules"]);
  expect([laptop.clicks.rules, tas.clicks.rules]).toEqual([1, 1]);
});

it("an older app following an up-to-date host: its screen follows, and its clicks reach the group through the leader", async () => {
  const tas = page("tas", "mainpc", "now");
  const laptop = page("iliya", "laptop", "1.2.433");
  await goLive(tas, { device: "", leading: true, older: ["laptop"] });
  await goLive(laptop, { device: "mainpc" });  // 1.2.433's Follow tas's screen
  await tas.click("video");
  expect([laptop.shown(), tas.shown()]).toEqual(["video", "video"]);
  await laptop.click("rules");
  expect([laptop.shown(), tas.shown()]).toEqual(["rules", "rules"]);
  expect([laptop.clicks.rules, tas.clicks.rules]).toEqual([1, 1]);  // nobody relays it: never twice
});

it("someone who joins goes to the group's screen, and the group never goes to theirs", async () => {
  const laptop = page("iliya", "laptop", "now");
  await goLive(laptop, { device: "", leading: true, older: [] });
  await laptop.click("video");  // the group is on Video
  const tas = page("tas", "mainpc", "now");
  await tas.click("rules");  // tas was on Rules before joining
  tell(tas, { type: "live.follow", device: "laptop", leading: false, older: [] });
  tell(tas, { type: "live.state", on: true });
  await settle();
  expect(laptop.shown()).toBe("video");  // the joiner's own screen moved nobody
  tell(laptop, { type: "live.peers", peers: [{ name: "tas", device: "mainpc", path: "direct" }] });  // the link to tas is up
  await settle();
  expect([laptop.shown(), tas.shown()]).toEqual(["video", "video"]);
  await new Promise((resolve) => setTimeout(resolve, 100));  // past the catch-up's own scroll
  await tas.click("thumbs");  // from then on, tas's own moves go to the group as before
  expect([laptop.shown(), tas.shown()]).toEqual(["thumbs", "thumbs"]);
});
