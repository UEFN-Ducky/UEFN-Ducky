import { afterEach, describe, expect, it, vi } from "vitest";
import {
  _resetWalkthroughServiceForTests,
  autoStartPending,
  completeTour,
  getActiveSteps,
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
      onCompleteStart: "settings.store",
    });
    registerTour({
      id: "settings.store",
      steps: [{ target: "b", title: "B", body: "b", advance: "next" }],
    });

    await startTour("app.shell", { force: true });
    expect(getWalkthroughState().tourId).toBe("app.shell");
    await completeTour();
    expect(isCompleted("app.shell")).toBe(true);
    expect(getWalkthroughState().active).toBe(false);

    await vi.advanceTimersByTimeAsync(500);
    expect(getWalkthroughState().tourId).toBe("settings.store");
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

  it("skip on first-run chain dismisses shell/store/llms and does not mark chat", async () => {
    vi.useFakeTimers();
    registerTour({
      id: "app.shell",
      steps: [{ target: "a", title: "A", body: "a", advance: "next" }],
      onCompleteStart: "settings.store",
    });
    registerTour({
      id: "settings.store",
      steps: [{ target: "b", title: "B", body: "b", advance: "next" }],
    });
    registerTour({
      id: "chat.composer",
      steps: [{ target: "c", title: "C", body: "c", advance: "next" }],
    });
    await startTour("app.shell", { force: true });
    await skipTour();
    expect(isCompleted("app.shell")).toBe(true);
    expect(isCompleted("settings.store")).toBe(true);
    expect(isCompleted("llms.setup")).toBe(true);
    expect(isCompleted("plugin.anthropic")).toBe(true);
    expect(isCompleted("chat.composer")).toBe(false);
    expect(isCompleted("settings.core")).toBe(false);
    await vi.advanceTimersByTimeAsync(500);
    expect(getWalkthroughState().active).toBe(false);
  });

  it("skip on a starter gateway tour dismisses the other starter gateways", async () => {
    registerTour({
      id: "plugin.anthropic",
      steps: [{ target: "a", title: "A", body: "a", advance: "next" }],
    });
    await startTour("plugin.anthropic", { force: true });
    await skipTour();
    expect(isCompleted("plugin.anthropic")).toBe(true);
    expect(isCompleted("plugin.cursor")).toBe(true);
    expect(isCompleted("plugin.openai")).toBe(true);
    expect(isCompleted("llms.setup")).toBe(true);
    expect(isCompleted("app.shell")).toBe(false);
  });

  it("skip on chat marks only that tour", async () => {
    registerTour({
      id: "chat.composer",
      steps: [{ target: "c", title: "C", body: "c", advance: "next" }],
    });
    await startTour("chat.composer", { force: true });
    await skipTour();
    expect(isCompleted("chat.composer")).toBe(true);
    expect(isCompleted("app.shell")).toBe(false);
    expect(isCompleted("settings.store")).toBe(false);
  });

  it("autoStartPending is a no-op; first-run starts from starter LLM onboard", () => {
    registerTour({
      id: "app.shell",
      steps: [{ target: "a", title: "A", body: "a", advance: "next" }],
      autoStart: "first_incomplete",
    });
    autoStartPending();
    expect(isCompleted("app.shell")).toBe(false);
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

  it("migrates settings.core completion onto the split Settings tours", () => {
    setCompletedMap({ "settings.core": true, "app.shell": true });
    expect(isCompleted("settings.general")).toBe(true);
    expect(isCompleted("settings.duckies")).toBe(true);
    expect(isCompleted("settings.plans")).toBe(true);
    expect(isCompleted("settings.llms")).toBe(true);
    expect(isCompleted("settings.appearance")).toBe(true);
    expect(isCompleted("settings.audio")).toBe(true);
    expect(isCompleted("chat.composer")).toBe(false);
  });

  it("redoAppWalkthrough clears first-run chain and starts app.shell", async () => {
    registerTour({
      id: "app.shell",
      steps: [{ target: "a", title: "A", body: "a", advance: "next" }],
      onCompleteStart: "settings.store",
    });
    registerTour({
      id: "settings.store",
      steps: [{ target: "c", title: "C", body: "c", advance: "next" }],
    });
    registerTour({
      id: "chat.composer",
      steps: [{ target: "d", title: "D", body: "d", advance: "next" }],
    });
    setCompletedMap({
      "app.shell": true,
      "settings.store": true,
      "llms.setup": true,
      "chat.composer": true,
      "plugin.translation": true,
    });
    await redoAppWalkthrough();
    const map = getCompletedMap();
    expect(map["app.shell"]).toBeFalsy();
    expect(map["settings.store"]).toBeFalsy();
    expect(map["llms.setup"]).toBeFalsy();
    expect(map["chat.composer"]).toBe(true);
    expect(map["plugin.translation"]).toBe(true);
    expect(getWalkthroughState().tourId).toBe("app.shell");
  });

  it("startTour caches resolveSteps for the active run", async () => {
    let n = 0;
    registerTour({
      id: "dyn",
      steps: [{ target: "a", title: "A", body: "a", advance: "next" }],
      resolveSteps: () => {
        n += 1;
        return [
          { target: "x", title: "X", body: "x", advance: "next" },
          { target: "y", title: "Y", body: "y", advance: "next" },
        ];
      },
    });
    await startTour("dyn", { force: true });
    expect(n).toBe(1);
    expect(getActiveSteps().map((s) => s.target)).toEqual(["x", "y"]);
    expect(getWalkthroughState().tourId).toBe("dyn");
  });
});
