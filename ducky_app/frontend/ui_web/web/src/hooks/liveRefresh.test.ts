// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentEvent } from "../types/panel";

const bus = vi.hoisted(() => ({ listener: null as ((e: AgentEvent) => void) | null }));
vi.mock("./useAgentEventBus", () => ({
  subscribeAgentEvents: (fn: (e: AgentEvent) => void) => {
    bus.listener = fn;
    return () => { bus.listener = null; };
  },
}));
const { subscribeLiveRefresh } = await import("./liveRefresh");

let visibility: DocumentVisibilityState = "visible";
const setVisibility = (v: DocumentVisibilityState) => {
  visibility = v;
  document.dispatchEvent(new Event("visibilitychange"));
};
const emit = (type: string) => bus.listener?.({ type, conv_id: "c" } as AgentEvent);

beforeEach(() => {
  vi.useFakeTimers();
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
});
afterEach(() => vi.useRealTimers());

describe("subscribeLiveRefresh", () => {
  it("100 events in a second refetch once, 1.5 s after the last", () => {
    const refresh = vi.fn();
    const stop = subscribeLiveRefresh((e) => e.type === "changeset_row", refresh);
    for (let i = 0; i < 100; i++) {
      emit("changeset_row");
      emit("tool_done"); // not accepted: never schedules
      vi.advanceTimersByTime(10);
    }
    expect(refresh).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1500);
    expect(refresh).toHaveBeenCalledTimes(1);
    stop();
  });

  it("a steady stream still refetches within 5 s", () => {
    const refresh = vi.fn();
    const stop = subscribeLiveRefresh(() => true, refresh);
    for (let i = 0; i < 100; i++) {
      emit("changeset_row");
      vi.advanceTimersByTime(100);
    }
    expect(refresh).toHaveBeenCalledTimes(2); // at 5 s and 10 s
    stop();
  });

  it("a hidden window refetches nothing, then catches up once when shown", () => {
    const refresh = vi.fn();
    const stop = subscribeLiveRefresh(() => true, refresh);
    setVisibility("hidden");
    for (let i = 0; i < 50; i++) emit("changeset_row");
    vi.advanceTimersByTime(10_000);
    expect(refresh).not.toHaveBeenCalled();
    setVisibility("visible");
    expect(refresh).toHaveBeenCalledTimes(1);
    setVisibility("hidden");
    setVisibility("visible");
    expect(refresh).toHaveBeenCalledTimes(1);
    stop();
  });
});
