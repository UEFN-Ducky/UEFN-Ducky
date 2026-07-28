import { afterEach, describe, expect, it, vi } from "vitest";
import {
  _resetWalkthroughServiceForTests,
  autoStartPending,
  completeTour,
  getCompletedMap,
  getWalkthroughState,
  isCompleted,
  redoAppWalkthrough,
  redoTour,
  registerTour,
  setCompletedMap,
  skipTour,
  startTour,
} from "./WalkthroughService";

afterEach(() => {
  vi.useRealTimers();
  _resetWalkthroughServiceForTests();
});

describe("WalkthroughService", () => {
  it("chains onCompleteStart after complete", async () => {
    vi.useFakeTimers();
    registerTour({
      id: "app.shell",
      steps: [{ target: "a", title: "A", body: "a", advance: "next" }],
      onCompleteStart: "settings.core",
    });
    registerTour({
      id: "settings.core",
      steps: [{ target: "b", title: "B", body: "b", advance: "next" }],
    });

    await startTour("app.shell", { force: true });
    expect(getWalkthroughState().tourId).toBe("app.shell");
    await completeTour();
    expect(isCompleted("app.shell")).toBe(true);
    expect(getWalkthroughState().active).toBe(false);

    await vi.advanceTimersByTimeAsync(500);
    expect(getWalkthroughState().tourId).toBe("settings.core");
  });

  it("skip marks completed and does not leave tour active", async () => {
    registerTour({
      id: "settings.store",
      steps: [
        { target: "a", title: "A", body: "a", advance: "next" },
        { target: "b", title: "B", body: "b", advance: "next" },
      ],
    });
    await startTour("settings.store", { force: true });
    await skipTour();
    expect(isCompleted("settings.store")).toBe(true);
    expect(getWalkthroughState().active).toBe(false);
  });

  it("skip on host chain dismisses all host tours and does not chain", async () => {
    vi.useFakeTimers();
    registerTour({
      id: "app.shell",
      steps: [{ target: "a", title: "A", body: "a", advance: "next" }],
      onCompleteStart: "settings.core",
    });
    registerTour({
      id: "settings.core",
      steps: [{ target: "b", title: "B", body: "b", advance: "next" }],
    });
    await startTour("app.shell", { force: true });
    await skipTour();
    expect(isCompleted("app.shell")).toBe(true);
    expect(isCompleted("settings.core")).toBe(true);
    expect(isCompleted("settings.store")).toBe(true);
    await vi.advanceTimersByTimeAsync(500);
    expect(getWalkthroughState().active).toBe(false);
  });

  it("autoStartPending marks complete before showing so relaunch will not re-offer", async () => {
    registerTour({
      id: "app.shell",
      steps: [{ target: "a", title: "A", body: "a", advance: "next" }],
      autoStart: "first_incomplete",
    });
    autoStartPending();
    expect(isCompleted("app.shell")).toBe(true);
    expect(getWalkthroughState().tourId).toBe("app.shell");
    await skipTour();
    autoStartPending();
    expect(getWalkthroughState().active).toBe(false);
  });

  it("redo clears flag and restarts", async () => {
    registerTour({
      id: "plugin.translation",
      steps: [{ target: "a", title: "A", body: "a", advance: "next" }],
    });
    setCompletedMap({ "plugin.translation": true });
    expect(isCompleted("plugin.translation")).toBe(true);
    await redoTour("plugin.translation");
    expect(isCompleted("plugin.translation")).toBe(false);
    expect(getWalkthroughState().tourId).toBe("plugin.translation");
  });

  it("redoAppWalkthrough clears host chain and starts app.shell", async () => {
    registerTour({
      id: "app.shell",
      steps: [{ target: "a", title: "A", body: "a", advance: "next" }],
      onCompleteStart: "settings.core",
    });
    registerTour({
      id: "settings.core",
      steps: [{ target: "b", title: "B", body: "b", advance: "next" }],
    });
    registerTour({
      id: "settings.store",
      steps: [{ target: "c", title: "C", body: "c", advance: "next" }],
    });
    setCompletedMap({
      "app.shell": true,
      "settings.core": true,
      "settings.store": true,
      "plugin.translation": true,
    });
    await redoAppWalkthrough();
    const map = getCompletedMap();
    expect(map["app.shell"]).toBeFalsy();
    expect(map["settings.core"]).toBeFalsy();
    expect(map["settings.store"]).toBeFalsy();
    expect(map["plugin.translation"]).toBe(true);
    expect(getWalkthroughState().tourId).toBe("app.shell");
  });
});
