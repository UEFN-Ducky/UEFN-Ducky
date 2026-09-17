import { beforeEach, describe, expect, it, vi } from "vitest";
import { canSnip, captureSnipFile } from "./snipCapture";

const { isRemote, getApi } = vi.hoisted(() => ({
  isRemote: vi.fn(() => false),
  getApi: vi.fn(() => ({ snip_screen: vi.fn() })),
}));

vi.mock("../hooks/usePanelApi", () => ({ getApi, isRemote }));

describe("canSnip", () => {
  beforeEach(() => {
    isRemote.mockReturnValue(false);
    getApi.mockReturnValue({ snip_screen: vi.fn() });
  });

  it("is on in the desktop app when snip_screen exists", () => {
    expect(canSnip()).toBe(true);
  });

  it("is off on the web stream even if the proxy exposes snip_screen", () => {
    isRemote.mockReturnValue(true);
    expect(canSnip()).toBe(false);
  });
});

describe("captureSnipFile", () => {
  it("does not call snip_screen on the web stream", async () => {
    const snip_screen = vi.fn();
    isRemote.mockReturnValue(true);
    getApi.mockReturnValue({ snip_screen });
    expect(await captureSnipFile()).toBeNull();
    expect(snip_screen).not.toHaveBeenCalled();
  });
});
