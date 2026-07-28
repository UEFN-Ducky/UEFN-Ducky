import { afterEach, describe, expect, it, vi } from "vitest";
import {
  _peekStoreJobForTests,
  _resetStoreInstallJobsForTests,
  beginStoreInstall,
  clearStoreJobLater,
  countWorkingStoreJobs,
  dismissAllStoreJobToasts,
  endStoreInstall,
  formatStoreJobBadge,
  hideStoreJobToast,
  isStoreInstallBusy,
  isStoreInstallQueueActive,
  markStoreCatalogDirty,
  patchStoreJob,
  reclaimOrphanStoreJobs,
  revealAllStoreJobToasts,
  revealStoreJobToast,
  runStoreInstallExclusive,
  setStoreInstallQueueIdleHandler,
  takeStoreCatalogDirty,
} from "./storeInstallJobs";
import { installProgressPct } from "../views/settings/store/storeData";

afterEach(() => {
  _resetStoreInstallJobsForTests();
  vi.useRealTimers();
});

describe("storeInstallJobs", () => {
  it("survives begin/patch across 'remount' — runner stays until endStoreInstall", () => {
    expect(beginStoreInstall("studio3d")).toBe(true);
    expect(beginStoreInstall("studio3d")).toBe(false);
    patchStoreJob("studio3d", {
      slug: "studio3d",
      label: "Downloading…",
      phase: "working",
      step: "download",
      name: "3D AI Studio",
    });
    expect(isStoreInstallBusy("studio3d")).toBe(true);
    endStoreInstall("studio3d");
    // Overlay may still show working, but no live runner — reclaim can restart.
    expect(isStoreInstallBusy("studio3d")).toBe(false);
    expect(_peekStoreJobForTests("studio3d")?.phase).toBe("working");
    patchStoreJob("studio3d", null);
    expect(_peekStoreJobForTests("studio3d")).toBeUndefined();
  });

  it("clearStoreJobLater removes done jobs from the Updates list after a short flash", () => {
    vi.useFakeTimers();
    hideStoreJobToast("meshy");
    clearStoreJobLater("meshy", {
      slug: "meshy",
      label: "Updated",
      phase: "done",
      step: "done",
    });
    expect(isStoreInstallBusy("meshy")).toBe(false);
    expect(_peekStoreJobForTests("meshy")?.phase).toBe("done");
    vi.advanceTimersByTime(2_199);
    expect(_peekStoreJobForTests("meshy")?.phase).toBe("done");
    vi.advanceTimersByTime(1);
    expect(_peekStoreJobForTests("meshy")).toBeUndefined();
  });

  it("dismissAllStoreJobToasts drops finished and hides working", () => {
    patchStoreJob("a", {
      slug: "a",
      label: "Downloading…",
      phase: "working",
      step: "download",
    });
    patchStoreJob("b", {
      slug: "b",
      label: "Updated",
      phase: "done",
      step: "done",
    });
    dismissAllStoreJobToasts();
    expect(_peekStoreJobForTests("a")?.phase).toBe("working");
    expect(_peekStoreJobForTests("b")).toBeUndefined();
    revealAllStoreJobToasts();
  });

  it("runs Update All installs one at a time (keeps UI from freezing)", async () => {
    const order: string[] = [];
    let releaseA!: () => void;
    const gateA = new Promise<void>((r) => {
      releaseA = r;
    });
    const a = runStoreInstallExclusive(async () => {
      order.push("a-start");
      await gateA;
      order.push("a-end");
      return "a";
    });
    const b = runStoreInstallExclusive(async () => {
      order.push("b");
      return "b";
    });
    await Promise.resolve();
    expect(order).toEqual(["a-start"]);
    expect(isStoreInstallQueueActive()).toBe(true);
    releaseA();
    await expect(a).resolves.toBe("a");
    await expect(b).resolves.toBe("b");
    expect(order).toEqual(["a-start", "a-end", "b"]);
    expect(isStoreInstallQueueActive()).toBe(false);
  });

  it("calls idle handler once when exclusive queue drains", async () => {
    const idle = vi.fn(async () => {
      expect(takeStoreCatalogDirty()).toBe(true);
    });
    setStoreInstallQueueIdleHandler(idle);
    markStoreCatalogDirty();
    await runStoreInstallExclusive(async () => "x");
    expect(idle).toHaveBeenCalledTimes(1);
  });

  it("queued jobs show tiny progress, not fake download %", () => {
    expect(
      installProgressPct({
        slug: "x",
        label: "Waiting…",
        phase: "working",
        step: "download",
        queued: true,
      }),
    ).toBe(4);
    expect(
      installProgressPct({
        slug: "x",
        label: "Downloading…",
        phase: "working",
        step: "download",
      }),
    ).toBeGreaterThan(4);
  });

  it("formats job badge as 8+", () => {
    expect(formatStoreJobBadge(0)).toBe("");
    expect(formatStoreJobBadge(3)).toBe("3");
    expect(formatStoreJobBadge(8)).toBe("8");
    expect(formatStoreJobBadge(9)).toBe("8+");
    expect(
      countWorkingStoreJobs({
        a: { slug: "a", label: "", phase: "working", step: "download" },
        b: { slug: "b", label: "", phase: "done", step: "done" },
      }),
    ).toBe(1);
  });

  it("reclaimOrphanStoreJobs only resumes jobs without a live runner", () => {
    beginStoreInstall("live");
    patchStoreJob("live", {
      slug: "live",
      label: "Downloading…",
      phase: "working",
      step: "download",
    });
    patchStoreJob("orphan", {
      slug: "orphan",
      label: "Waiting…",
      phase: "working",
      step: "download",
      queued: true,
    });
    const resumed: string[] = [];
    reclaimOrphanStoreJobs((slug) => resumed.push(slug));
    expect(resumed).toEqual(["orphan"]);
    endStoreInstall("live");
  });
});
