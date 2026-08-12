import { beforeEach, describe, expect, it, vi } from "vitest";

const store = new Map<string, string>();
const localStorageMock = {
  getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
  setItem: (k: string, v: string) => {
    store.set(k, String(v));
  },
  removeItem: (k: string) => {
    store.delete(k);
  },
  clear: () => {
    store.clear();
  },
};
vi.stubGlobal("localStorage", localStorageMock);
vi.stubGlobal("window", {
  dispatchEvent: () => true,
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
});

const saveAgentSettings = vi.fn(async () => "ok");
const getSettings = vi.fn(async () => ({
  follow_code_enabled: true,
  follow_code_speed: "normal",
  follow_code_split_beside_chat: true,
}));

vi.mock("../../hooks/usePanelApi", () => ({
  getApi: () => ({
    get_settings: getSettings,
    save_agent_settings: saveAgentSettings,
  }),
}));

describe("followCodeSettings persistence", () => {
  beforeEach(async () => {
    store.clear();
    saveAgentSettings.mockClear();
    getSettings.mockClear();
    getSettings.mockResolvedValue({
      follow_code_enabled: true,
      follow_code_speed: "normal",
      follow_code_split_beside_chat: true,
    });
    vi.resetModules();
  });

  it("keeps disabled in memory and saves to panel settings", async () => {
    const mod = await import("./followCodeSettings");
    mod.setFollowCodeSettings({ enabled: false });
    expect(mod.getFollowCodeSettings().enabled).toBe(false);
    expect(saveAgentSettings).toHaveBeenCalledWith(
      expect.objectContaining({ follow_code_enabled: false }),
    );
  });

  it("does not let a stale load overwrite a newer toggle", async () => {
    let resolveSettings!: (v: unknown) => void;
    getSettings.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSettings = resolve;
        }),
    );
    const mod = await import("./followCodeSettings");
    const loadPromise = mod.loadFollowCodeSettings();
    mod.setFollowCodeSettings({ enabled: false });
    resolveSettings({
      follow_code_enabled: true,
      follow_code_speed: "normal",
      follow_code_split_beside_chat: true,
    });
    await loadPromise;
    expect(mod.getFollowCodeSettings().enabled).toBe(false);
  });

  it("migrates a legacy localStorage disable into panel settings once", async () => {
    localStorage.setItem("uefn-follow-code-enabled", "false");
    const mod = await import("./followCodeSettings");
    await mod.loadFollowCodeSettings();
    expect(mod.getFollowCodeSettings().enabled).toBe(false);
    expect(saveAgentSettings).toHaveBeenCalledWith(
      expect.objectContaining({ follow_code_enabled: false }),
    );
    expect(localStorage.getItem("uefn-follow-code-migrated-v1")).toBe("1");
  });
});
