import { afterEach, describe, expect, it, vi } from "vitest";
import { httpsUrl, openHttpsOnThisDevice, UD_OPEN_URL } from "./openHttps";

describe("httpsUrl", () => {
  it("keeps https and rejects the rest", () => {
    expect(httpsUrl("https://example.com/a")).toBe("https://example.com/a");
    expect(httpsUrl("  HTTPS://example.com  ")).toBe("HTTPS://example.com");
    expect(httpsUrl("http://example.com")).toBe("");
    expect(httpsUrl("javascript:alert(1)")).toBe("");
    expect(httpsUrl("")).toBe("");
  });
});

describe("openHttpsOnThisDevice", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("opens https via window.open", () => {
    const open = vi.fn();
    vi.stubGlobal("open", open);
    expect(openHttpsOnThisDevice("https://docs.fortnite.com")).toBe(true);
    expect(open).toHaveBeenCalledWith("https://docs.fortnite.com", "_blank", "noopener,noreferrer");
  });

  it("no-ops javascript and http", () => {
    const open = vi.fn();
    vi.stubGlobal("open", open);
    expect(openHttpsOnThisDevice("javascript:alert(1)")).toBe(false);
    expect(openHttpsOnThisDevice("http://example.com")).toBe(false);
    expect(open).not.toHaveBeenCalled();
  });

  it("posts ud-open-url to the /ducky parent when framed", () => {
    const open = vi.fn();
    const postMessage = vi.fn();
    vi.stubGlobal("open", open);
    vi.stubGlobal("parent", { postMessage });
    expect(openHttpsOnThisDevice("https://example.com/x")).toBe(true);
    expect(postMessage).toHaveBeenCalledWith(
      { type: UD_OPEN_URL, url: "https://example.com/x" },
      "https://uefnducky.org",
    );
    expect(open).toHaveBeenCalled();
  });
});
