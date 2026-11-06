// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentEvent } from "../types/panel";

/** One agent turn the way the bus sees it: thinking, tools, streamed text, done. */
function turn(conv: string, run: string): AgentEvent[] {
  const events: AgentEvent[] = [{ type: "agent_started", conv_id: conv, run_id: run }];
  for (let k = 0; k < 4; k++) {
    events.push({ type: "thinking", conv_id: conv, run_id: run, text: "planning ".repeat(20) });
    events.push({ type: "tool", conv_id: conv, run_id: run, text: `read file${k}` });
    events.push({ type: "tool_done", conv_id: conv, run_id: run, success: true, text: "x".repeat(4000) });
    for (let d = 0; d < 10; d++) events.push({ type: "text_delta", conv_id: conv, run_id: run, text: `w${d} ` });
    events.push({ type: "status", conv_id: conv, run_id: run, text: `step ${k}` });
  }
  events.push({ type: "assistant_done", conv_id: conv, run_id: run });
  return events;
}

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  vi.stubGlobal("fetch", () => new Promise(() => {})); // the bus's event poll never answers here
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("renderer memory over a long session", () => {
  it("a hidden window (no frames) keeps its agent event queue bounded for 60 minutes of 20 chats", async () => {
    // Hidden or minimized: WebView2 paints nothing, so a frame is asked for and never comes.
    vi.stubGlobal("requestAnimationFrame", () => 0);
    const bus = await import("../hooks/useAgentEventBus");
    const heard = new Map<string, number>();
    bus.subscribeAgentEvents((e) => heard.set(e.conv_id!, (heard.get(e.conv_id!) ?? 0) + 1));
    let peak = 0;
    let pushed = 0;
    // One turn every 2 s, round-robin over 20 chats; Chrome runs a hidden page's timers about
    // once a second, so the clock moves 1 s at a time between bursts.
    for (let second = 0; second < 60 * 60; second += 2) {
      const conv = `chat-${(second / 2) % 20}`;
      for (const e of turn(conv, `run-${second}`)) {
        bus.pushLocalAgentEvent(e);
        pushed++;
      }
      peak = Math.max(peak, bus.pendingAgentEventCount());
      vi.advanceTimersByTime(1000);
      vi.advanceTimersByTime(1000);
    }
    vi.advanceTimersByTime(bus.NO_FRAME_FALLBACK_MS);
    expect(pushed).toBeGreaterThan(100_000);
    // Before the fix every one of those events waited for a frame that never came.
    expect(bus.pendingAgentEventCount()).toBe(0);
    expect(peak).toBeLessThanOrEqual(turn("c", "r").length);
    expect(heard.size).toBe(20);
  });

  it("a visible window still delivers on its frame, not on the fallback timer", async () => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
    const bus = await import("../hooks/useAgentEventBus");
    const heard: string[] = [];
    bus.subscribeAgentEvents((e) => heard.push(e.text ?? ""));
    bus.pushLocalAgentEvent({ type: "tool", conv_id: "c", run_id: "r", text: "a" });
    expect(frames).toHaveLength(1);
    frames.shift()!(performance.now());
    expect(heard).toEqual(["a"]);
    vi.advanceTimersByTime(bus.NO_FRAME_FALLBACK_MS * 2); // the fallback is a no-op now
    expect(heard).toEqual(["a"]);
  });

  it("a frame that stops coming mid-stream (the window was hidden) is covered by the timer", async () => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
    const bus = await import("../hooks/useAgentEventBus");
    const heard: string[] = [];
    bus.subscribeAgentEvents((e) => heard.push(e.text ?? ""));
    bus.pushLocalAgentEvent({ type: "tool", conv_id: "c", run_id: "r", text: "a" });
    bus.pushLocalAgentEvent({ type: "tool", conv_id: "c", run_id: "r", text: "b" });
    vi.advanceTimersByTime(bus.NO_FRAME_FALLBACK_MS);
    expect(heard).toEqual(["a", "b"]);
    expect(bus.pendingAgentEventCount()).toBe(0);
    frames.shift()!(performance.now()); // the late frame delivers nothing twice
    expect(heard).toEqual(["a", "b"]);
  });
});
