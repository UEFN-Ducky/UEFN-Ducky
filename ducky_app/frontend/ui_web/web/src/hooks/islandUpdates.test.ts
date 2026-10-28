// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { _resetBackgroundActivityForTests, getBackgroundJobs } from "./backgroundActivity";
import { _resetIslandUpdatesForTests, installIslandUpdates } from "./islandUpdates";

const push = vi.hoisted(() => ({ handlers: new Set<(event: Record<string, unknown>) => void>() }));
vi.mock("./usePanelPushBus", () => ({
  installPanelPushBus: () => {},
  subscribePanelPush: (fn: (event: Record<string, unknown>) => void) => {
    push.handlers.add(fn);
    return () => push.handlers.delete(fn);
  },
}));
const send = (event: Record<string, unknown>) => push.handlers.forEach((fn) => fn(event));

afterEach(() => {
  _resetIslandUpdatesForTests();
  _resetBackgroundActivityForTests();
});

describe("island updates", () => {
  it("one row per island: new changes, then up to date", () => {
    installIslandUpdates();
    installIslandUpdates();
    const island = "C:\\Islands\\test_ducky_v2";
    send({ type: "island_updates", island, name: "test_ducky_v2", behind: true, count: 2,
      detail: "2 new changes on test_ducky_v2 from Mich ou" });
    let jobs = getBackgroundJobs();
    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({ id: `island-updates:${island}`, title: "test_ducky_v2", phase: "done" });
    expect(jobs[0].detail).toContain("2 new changes on test_ducky_v2 from Mich ou. Ask a Ducky to pull them");
    send({ type: "island_updates", island, name: "test_ducky_v2", behind: false, count: 0,
      detail: "test_ducky_v2 is up to date." });
    jobs = getBackgroundJobs();
    expect(jobs).toHaveLength(1);
    expect(jobs[0].detail).toBe("test_ducky_v2 is up to date.");
  });
});
