// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../hooks/usePanelApi", () => ({ getApi: () => ({}) }));
vi.mock("./openSettingsTab", () => ({ requestOpenSettings: vi.fn() }));

import { requestOpenSettings } from "./openSettingsTab";
import {
  ACCOUNT_INSTALL_HINT_KEY,
  ACCOUNT_LOGIN_EVENT,
  ACCOUNT_LOGIN_KEY,
  consumeStoreInstallRequest,
  handleDeepLink,
  peekStoreInstallRequest,
  STORE_INSTALL_KEY,
} from "./deepLinks";

describe("uefn-ducky://login", () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.mocked(requestOpenSettings).mockReset();
  });

  it("opens Plugins → Account details and starts pairing when installed", () => {
    const onLogin = vi.fn();
    window.addEventListener(ACCOUNT_LOGIN_EVENT, onLogin);

    expect(handleDeepLink("uefn-ducky://login")).toBe(true);
    expect(requestOpenSettings).toHaveBeenCalledWith("Store", { storeSlug: "account" });
    expect(JSON.parse(sessionStorage.getItem(STORE_INSTALL_KEY) || "{}")).toEqual({
      slug: "account",
      install: false,
    });
    expect(sessionStorage.getItem(ACCOUNT_LOGIN_KEY)).toBe("1");
    expect(sessionStorage.getItem(ACCOUNT_INSTALL_HINT_KEY)).toBe("1");
    expect(onLogin).toHaveBeenCalled();
    window.removeEventListener(ACCOUNT_LOGIN_EVENT, onLogin);
  });

  it("opens Plugins → Account details when the plugin is missing", () => {
    expect(handleDeepLink("uefn-ducky://login")).toBe(true);
    expect(requestOpenSettings).toHaveBeenCalledWith("Store", { storeSlug: "account" });
    expect(JSON.parse(sessionStorage.getItem(STORE_INSTALL_KEY) || "{}")).toEqual({
      slug: "account",
      install: false,
    });
    expect(sessionStorage.getItem(ACCOUNT_INSTALL_HINT_KEY)).toBe("1");
  });
});

describe("store install handshake", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("peeks without consuming so a missing catalog row can retry", () => {
    sessionStorage.setItem(STORE_INSTALL_KEY, JSON.stringify({ slug: "account", install: false }));
    expect(peekStoreInstallRequest()).toEqual({ slug: "account", autoInstall: false });
    expect(sessionStorage.getItem(STORE_INSTALL_KEY)).toBeTruthy();
    expect(consumeStoreInstallRequest()).toEqual({ slug: "account", autoInstall: false });
    expect(peekStoreInstallRequest()).toBeNull();
  });
});
