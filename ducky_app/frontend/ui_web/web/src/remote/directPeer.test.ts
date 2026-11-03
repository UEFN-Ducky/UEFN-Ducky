// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

const api: { remote_deny_methods?: () => unknown; direct_rtc_answer?: (...a: unknown[]) => unknown } = {};
vi.mock("../hooks/usePanelApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../hooks/usePanelApi")>()),
  getApi: () => api,
}));

import { DENY_WAIT_MS, deniedMethods, forgetDeniedMethodsForTest, installDirectPeerOnDesktop, onInput } from "./directPeer";
import { pushLocalAgentEvent } from "../hooks/useAgentEventBus";
import type { AgentEvent } from "../types/panel";

describe("installDirectPeerOnDesktop", () => {
  it("answers offers once pywebview's bridge arrives after the app mounted", async () => {
    const w = window as unknown as { chrome?: unknown; pywebview?: unknown };
    w.chrome = { webview: {} }; // the desktop's WebView2, before pywebview injected its bridge
    vi.stubGlobal("fetch", () => new Promise(() => {})); // the bus's event poll never answers here
    vi.stubGlobal(
      "RTCPeerConnection",
      class {
        setRemoteDescription() {
          return Promise.reject(new Error("bad offer"));
        }
        close() {}
      },
    );
    const answer = vi.fn();
    api.direct_rtc_answer = answer;
    const offer = (session: string) =>
      ({ type: "direct_rtc", session, offer: { type: "offer", sdp: "v=0" }, ts: Date.now() / 1000 }) as unknown as AgentEvent;
    const stop = installDirectPeerOnDesktop();
    try {
      w.pywebview = { api };
      window.dispatchEvent(new Event("pywebviewready"));
      pushLocalAgentEvent(offer("s1"));
      await vi.waitFor(() => expect(answer).toHaveBeenCalledWith("s1", null, "", "bad offer"));
    } finally {
      stop();
      delete w.pywebview;
      delete w.chrome;
      vi.unstubAllGlobals();
    }
  });
});

describe("onInput", () => {
  it("applies the input without its seq and echoes the seq once applied", async () => {
    const applied: unknown[] = [];
    (api as { window_input?: unknown }).window_input = (hwnd: unknown, ev: unknown) => {
      applied.push([hwnd, ev]);
      return Promise.resolve();
    };
    const ch = { readyState: "open" as RTCDataChannelState, sent: [] as string[], send(s: string) {
      this.sent.push(s);
    } };
    await onInput(ch, 7, JSON.stringify({ type: "tap", x: 1, seq: 42 }));
    await onInput(ch, 7, JSON.stringify({ type: "tap", x: 2 }));
    expect(applied).toEqual([[7, { type: "tap", x: 1 }], [7, { type: "tap", x: 2 }]]);
    expect(ch.sent).toEqual([JSON.stringify({ echo: 42 })]);
  });

  it("still echoes a probe when no window is watched, so the phone does not count it lost", async () => {
    const ch = { readyState: "open" as RTCDataChannelState, sent: [] as string[], send(s: string) {
      this.sent.push(s);
    } };
    await onInput(ch, undefined, JSON.stringify({ type: "tap", seq: 3 }));
    await onInput(ch, undefined, "not json");
    expect(ch.sent).toEqual([JSON.stringify({ echo: 3 })]);
  });
});

describe("deniedMethods", () => {
  afterEach(() => {
    forgetDeniedMethodsForTest();
    vi.useRealTimers();
  });

  it("gives up on a reply that never comes and asks again next time", async () => {
    vi.useFakeTimers();
    api.remote_deny_methods = () => new Promise(() => {});
    const first = deniedMethods();
    await vi.advanceTimersByTimeAsync(DENY_WAIT_MS);
    expect(await first).toBeNull();
    api.remote_deny_methods = () => Promise.resolve(["set_uefn_plugin_secret"]);
    expect(await deniedMethods()).toEqual(new Set(["set_uefn_plugin_secret"]));
  });

  it("keeps the list once Ducky has answered", async () => {
    let asks = 0;
    api.remote_deny_methods = () => {
      asks += 1;
      return Promise.resolve(["test_key"]);
    };
    await deniedMethods();
    await deniedMethods();
    expect(asks).toBe(1);
  });

  it("fails closed when Ducky gives no list", async () => {
    api.remote_deny_methods = undefined;
    expect(await deniedMethods()).toBeNull();
  });
});
