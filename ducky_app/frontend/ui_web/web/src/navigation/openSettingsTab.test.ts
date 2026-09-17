// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { registerSettingsTabConsumer, requestOpenSettings } from "./openSettingsTab";

describe("requestOpenSettings debounce", () => {
  let t = 0;

  beforeEach(() => {
    t += 1000;
    vi.useFakeTimers();
    vi.setSystemTime(t);
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("drops identical tab+slug repeats within 500ms", () => {
    const fn = vi.fn();
    const stop = registerSettingsTabConsumer(fn);
    requestOpenSettings("Store", { storeSlug: "blender" });
    requestOpenSettings("Store", { storeSlug: "blender" });
    expect(fn).toHaveBeenCalledTimes(1);
    stop();
  });

  it("does not drop a different tab within 500ms", () => {
    const fn = vi.fn();
    const stop = registerSettingsTabConsumer(fn);
    requestOpenSettings("Account");
    requestOpenSettings("Store", { storeSlug: "blender" });
    expect(fn.mock.calls.map((c) => c[0])).toEqual(["Account", "Store"]);
    stop();
  });

  it("does not drop a different Store slug within 500ms", () => {
    const fn = vi.fn();
    const stop = registerSettingsTabConsumer(fn);
    requestOpenSettings("Store", { storeSlug: "meshy" });
    requestOpenSettings("Store", { storeSlug: "blender" });
    expect(fn).toHaveBeenCalledTimes(2);
    stop();
  });

  it("routes Account to Store while a store/<slug> handshake is pending", () => {
    sessionStorage.setItem("uefn-store-install", JSON.stringify({ slug: "blender", install: false }));
    const fn = vi.fn();
    const stop = registerSettingsTabConsumer(fn);
    requestOpenSettings("Account");
    expect(fn.mock.calls.map((c) => c[0])).toEqual(["Store"]);
    stop();
  });
});
