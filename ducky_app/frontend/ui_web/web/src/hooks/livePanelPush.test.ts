// @vitest-environment jsdom
/** Live events come from the app over the same HTTP poll as every other event. Plugin panels
 * (a teammate's click, the group's shared values, who holds what) and scope bars (who is
 * live) listen on the panel bus, so the poll must hand every one of them there: left on the
 * agent bus, they reached no panel and nothing live showed in any plugin. */
import { afterEach, expect, it, vi } from "vitest";
import { installAgentEventBus } from "./useAgentEventBus";
import { installPanelPushBus, subscribePanelPush } from "./usePanelPushBus";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("every live event, and a plugin's own push, reaches the panel bus plugin panels and bars listen on", async () => {
  const events = [
    { type: "live_message", plugin: "imk", topic: "ducky.nav", from: { device: "d1" }, data: { anchor: "#video" } },
    { type: "live_shared", plugin: "imk", key: "player", value: { playing: true }, by: { device: "d1" } },
    { type: "live_peers", plugin: "imk", on: true, peers: [] },
    { type: "live_holds", plugin: "imk", holds: [] },
    { type: "live_member_state", plugin: "imk", from: { device: "d1" }, state: { cam: [0, 0, 1] } },
    // A plugin's own push (Kanban's after a ducky's edit): its panels subscribe to it there.
    { type: "kanban_changed", plugin_id: "kanban", board: "b1" },
  ];
  let served = false;
  vi.stubGlobal("fetch", vi.fn(async () => {
    if (served) return new Promise<Response>(() => {});  // the next long poll waits
    served = true;
    return new Response(JSON.stringify({ cursor: events.length, head: 0, events }), { status: 200 });
  }));
  const got: string[] = [];
  installPanelPushBus();
  subscribePanelPush((event) => got.push(String(event.type)));
  installAgentEventBus();
  await vi.waitFor(() => expect(got).toEqual(events.map((e) => e.type)));
});
