import { afterEach, describe, expect, it } from "vitest";
import { __setAssetBaseForTest, assetBase, assetUrl, isSubdirectoryPanel } from "./assetBase";
import { rewriteLoopbackUrls } from "./protocol";

afterEach(() => __setAssetBaseForTest(null));

describe("assetUrl", () => {
  it("is a no-op at the origin root (desktop)", () => {
    __setAssetBaseForTest("/");
    expect(assetBase()).toBe("/");
    expect(isSubdirectoryPanel()).toBe(false);
    expect(assetUrl("plugin-ui/x/index.html")).toBe("/plugin-ui/x/index.html");
    expect(assetUrl("/plugin-ui/x/index.html")).toBe("/plugin-ui/x/index.html");
  });

  it("keeps desktop assets inside the panel directory (direct mode)", () => {
    const base = "/static/plugins/uefn-ducky/panel/";
    __setAssetBaseForTest(base);
    expect(isSubdirectoryPanel()).toBe(true);
    expect(assetUrl("plugin-ui/x/index.html")).toBe(`${base}plugin-ui/x/index.html`);
    // A root-relative path would escape the Service Worker's scope — normalize it.
    expect(assetUrl("/user-sounds/ding.mp3")).toBe(`${base}user-sounds/ding.mp3`);
  });

  it("leaves absolute and inline URLs alone", () => {
    __setAssetBaseForTest("/static/plugins/uefn-ducky/panel/");
    for (const url of [
      "https://example.com/a.png",
      "blob:https://example.com/abc",
      "data:image/png;base64,AAA",
      "//cdn.example.com/a.js",
    ]) {
      expect(assetUrl(url)).toBe(url);
    }
    expect(assetUrl("")).toBe("");
  });
});

describe("rewriteLoopbackUrls with a panel base", () => {
  it("rewrites desktop loopback URLs into the panel directory", () => {
    const base = "/static/plugins/uefn-ducky/panel/";
    const out = rewriteLoopbackUrls(
      {
        img: "http://127.0.0.1:4199/model-files/abc/x.png",
        shot: "http://localhost:4199/tool-captures/1.png",
        keep: "https://example.com/z",
      },
      ["http://127.0.0.1:4199", "http://localhost:4199"],
      base,
    );
    expect(out).toEqual({
      img: `${base}model-files/abc/x.png`,
      shot: `${base}tool-captures/1.png`,
      keep: "https://example.com/z",
    });
  });

  it("still produces root paths on the desktop", () => {
    const out = rewriteLoopbackUrls({ img: "http://127.0.0.1:4199/duckies/custom/a.png" }, [
      "http://127.0.0.1:4199",
    ]);
    expect(out).toEqual({ img: "/duckies/custom/a.png" });
  });
});
