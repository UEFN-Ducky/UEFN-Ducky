// @vitest-environment jsdom
/** The event poll retries forever while the page lives. It must end when stopped, and when
 * its window is gone (a test file's jsdom torn down): its retry timer then threw
 * "window is not defined" as an unhandled rejection and failed the whole vitest run. */
import { afterEach, expect, it, vi } from "vitest";
import { installAgentEventBus, stopAgentEventPoll } from "./useAgentEventBus";

afterEach(() => {
  stopAgentEventPoll();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("a stopped poll fires no further request", async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn(async () => {
    throw new Error("offline");
  });
  vi.stubGlobal("fetch", fetchMock);
  installAgentEventBus();
  await vi.advanceTimersByTimeAsync(600); // first request fails, one 500 ms retry
  const calls = fetchMock.mock.calls.length;
  expect(calls).toBeGreaterThanOrEqual(2);
  stopAgentEventPoll();
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetchMock.mock.calls.length).toBe(calls);
});

it("a poll whose window is gone ends instead of throwing on its retry timer", async () => {
  let fail: (error: Error) => void = () => {};
  const fetchMock = vi.fn(() => new Promise<Response>((_, reject) => { fail = reject; }));
  vi.stubGlobal("fetch", fetchMock);
  installAgentEventBus();
  const unhandled = vi.fn();
  process.on("unhandledRejection", unhandled);
  try {
    vi.stubGlobal("window", undefined);
    fail(new Error("torn down"));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(unhandled).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  } finally {
    process.off("unhandledRejection", unhandled);
  }
});
