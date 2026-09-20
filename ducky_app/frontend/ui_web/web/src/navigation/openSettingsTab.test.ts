// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  openLlmsProviderSettings,
  registerSettingsTabConsumer,
  requestOpenSettings,
  takePendingLlmsProvider,
} from "./openSettingsTab";

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

  it("openLlmsProviderSettings opens LLMs and selects that provider", async () => {
    const fn = vi.fn();
    const stop = registerSettingsTabConsumer(fn);
    const sections: unknown[] = [];
    const providers: unknown[] = [];
    const onSection = (e: Event) => sections.push((e as CustomEvent).detail);
    const onProvider = (e: Event) => providers.push((e as CustomEvent).detail);
    window.addEventListener("ducky:settings-section", onSection);
    window.addEventListener("ducky:llms-select-provider", onProvider);
    openLlmsProviderSettings("ollama");
    expect(fn).toHaveBeenCalledWith("LLMs");
    expect(takePendingLlmsProvider()).toBe("ollama");
    await Promise.resolve();
    expect(sections).toEqual([{ tab: "LLMs", section: "llms" }]);
    expect(providers).toEqual([{ id: "ollama" }]);
    window.removeEventListener("ducky:settings-section", onSection);
    window.removeEventListener("ducky:llms-select-provider", onProvider);
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
