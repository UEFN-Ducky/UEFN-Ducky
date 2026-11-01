// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

const calls: string[] = [];

vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({
    capture_list_shield: async (on: boolean) => {
      calls.push(`shield:${on}`);
      return true;
    },
  }),
}));

afterEach(() => vi.useRealTimers());

it("blanks our window titles before getDisplayMedia and gives them back after the pick", async () => {
  // Oct 22 + 23 2026: Chromium's picker list thumbnailed the Ducky window itself and
  // deadlocked WebView2; an untitled window is never in that list.
  vi.useFakeTimers();
  const track = { readyState: "live", addEventListener: () => {}, stop: () => {} };
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      getDisplayMedia: async () => {
        calls.push("getDisplayMedia");
        return { getVideoTracks: () => [track] };
      },
    },
  });
  const { acquireScreenTrack, SHIELD_HOLD_MS } = await import("./remoteCapture");
  await expect(acquireScreenTrack()).resolves.toBe(track);
  expect(calls).toEqual(["shield:true", "getDisplayMedia"]);
  await vi.advanceTimersByTimeAsync(SHIELD_HOLD_MS - 1);
  expect(calls).toEqual(["shield:true", "getDisplayMedia"]);
  await vi.advanceTimersByTimeAsync(1);
  expect(calls).toEqual(["shield:true", "getDisplayMedia", "shield:false"]);
});

it("never asks getUserMedia for the screen (it raised WebView2's Screen Sharing prompt)", async () => {
  // Nov 1 2026: chromeMediaSource "screen" popped "Screen Sharing Request" on stream.
  vi.useFakeTimers();
  vi.resetModules();
  calls.length = 0;
  const track = { readyState: "live", addEventListener: () => {}, stop: () => {} };
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia: async () => {
        calls.push("getUserMedia");
        return { getVideoTracks: () => [track] };
      },
      getDisplayMedia: async () => {
        calls.push("getDisplayMedia");
        return { getVideoTracks: () => [track] };
      },
    },
  });
  const { acquireScreenTrack } = await import("./remoteCapture");
  await expect(acquireScreenTrack()).resolves.toBe(track);
  expect(calls).not.toContain("getUserMedia");
  expect(calls).toContain("getDisplayMedia");
});
