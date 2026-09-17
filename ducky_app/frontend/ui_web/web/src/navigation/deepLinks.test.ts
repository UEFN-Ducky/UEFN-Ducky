// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../hooks/usePanelApi", () => ({ getApi: () => ({}) }));
vi.mock("./openSettingsTab", () => ({ requestOpenSettings: vi.fn() }));

import { requestOpenSettings } from "./openSettingsTab";
import {
  consumeStoreInstallRequest,
  handleDeepLink,
  parseStoreDeepLink,
  peekStoreInstallRequest,
  STORE_INSTALL_KEY,
  storeHistoryYieldsToDeepLink,
} from "./deepLinks";

describe("uefn-ducky://store|plugin/<slug>", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.mocked(requestOpenSettings).mockReset();
  });

  it("opens Plugins details for any Store slug", () => {
    expect(handleDeepLink("uefn-ducky://store/blender")).toBe(true);
    expect(requestOpenSettings).toHaveBeenCalledWith("Store", { storeSlug: "blender" });
    expect(JSON.parse(sessionStorage.getItem(STORE_INSTALL_KEY) || "{}")).toEqual({
      slug: "blender",
      install: false,
    });
  });

  it("treats plugin/<slug> as the same hook", () => {
    expect(handleDeepLink("uefn-ducky://plugin/meshy")).toBe(true);
    expect(requestOpenSettings).toHaveBeenCalledWith("Store", { storeSlug: "meshy" });
    expect(parseStoreDeepLink("uefn-ducky://plugin/meshy")).toEqual({
      slug: "meshy",
      autoInstall: false,
    });
  });

  it("still auto-installs via store/install/<slug>", () => {
    expect(handleDeepLink("uefn-ducky://store/install/account")).toBe(true);
    expect(JSON.parse(sessionStorage.getItem(STORE_INSTALL_KEY) || "{}")).toEqual({
      slug: "account",
      install: true,
    });
  });
});

describe("storeHistoryYieldsToDeepLink", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("lets catalog history wait while a store/<slug> handshake is live", () => {
    sessionStorage.setItem(STORE_INSTALL_KEY, JSON.stringify({ slug: "blender", install: false }));
    expect(storeHistoryYieldsToDeepLink("")).toBe(true);
    expect(storeHistoryYieldsToDeepLink("meshy")).toBe(true);
    expect(storeHistoryYieldsToDeepLink("blender")).toBe(false);
  });

  it("does not yield when no handshake is pending", () => {
    expect(storeHistoryYieldsToDeepLink("")).toBe(false);
  });
});

describe("store install handshake", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("peeks without consuming so a missing catalog row can retry", () => {
    sessionStorage.setItem(STORE_INSTALL_KEY, JSON.stringify({ slug: "blender", install: false }));
    expect(peekStoreInstallRequest()).toEqual({ slug: "blender", autoInstall: false });
    expect(sessionStorage.getItem(STORE_INSTALL_KEY)).toBeTruthy();
    expect(consumeStoreInstallRequest()).toEqual({ slug: "blender", autoInstall: false });
    expect(peekStoreInstallRequest()).toBeNull();
  });
});
