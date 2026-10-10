// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ stat_project_file: vi.fn() }));
vi.mock("./usePanelApi", () => ({ getApi: () => api }));

import { useWatchProjectFile } from "./useWatchProjectFile";

let mtime = 1;

beforeEach(() => {
  vi.useFakeTimers();
  mtime = 1;
  api.stat_project_file.mockImplementation(async () => ({ exists: true, mtime_ns: mtime, size: 10 }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.resetAllMocks();
});

it("checks the open file every few seconds, not every second", async () => {
  renderHook(() => useWatchProjectFile("Content/a.verse", () => {}));
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  // It was 61 bridge calls a minute per editor group showing a file.
  expect(api.stat_project_file.mock.calls.length).toBeLessThanOrEqual(21);
});

it("checks at once when the window comes back into focus", async () => {
  const changed = vi.fn();
  renderHook(() => useWatchProjectFile("Content/a.verse", changed));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  mtime = 2; // saved from UEFN while Ducky was in the background
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(changed).toHaveBeenCalledTimes(1);
});
