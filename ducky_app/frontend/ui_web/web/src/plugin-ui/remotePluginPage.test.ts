// @vitest-environment jsdom
/** A plugin frame on the phone panel: its page is built from the desktop's files over
 * the PC link (a sandboxed frame never goes through the panel's Service Worker), while
 * the desktop keeps loading the page from its own panel server. */
import { describe, expect, it } from "vitest";
import { buildRemotePluginPage, desktopPathFor, pluginPageSource } from "./remotePluginPage";

const SITE_PANEL = "/static/plugins/uefn-ducky/panel/";

describe("where a plugin page comes from", () => {
  it("the desktop keeps its local panel URL", () => {
    const got = pluginPageSource("account", "ui/index.html", false, (p) => `/${p}`);
    expect(got).toEqual({ kind: "local", url: "/plugin-ui/account/ui/index.html" });
  });

  it("the phone panel builds the page over the PC link, never from the site's panel folder", () => {
    const got = pluginPageSource("account", "/ui/index.html", true, (p) => `${SITE_PANEL}${p}`);
    expect(got).toEqual({ kind: "link", path: "/plugin-ui/account/ui/index.html" });
    expect(JSON.stringify(got)).not.toContain(SITE_PANEL);
  });

  it("only the desktop's plugin files are fetched over the link", () => {
    const page = "/plugin-ui/account/ui/index.html";
    expect(desktopPathFor("account.js", page)).toBe("/plugin-ui/account/ui/account.js");
    expect(desktopPathFor("../../_kit/ducky.js", page)).toBe("/plugin-ui/_kit/ducky.js");
    expect(desktopPathFor("../../../__panel_api/list_chats", page)).toBe("");
    expect(desktopPathFor("https://example.com/x.js", page)).toBe("");
    expect(desktopPathFor("data:text/plain,hi", page)).toBe("");
    expect(desktopPathFor("#top", page)).toBe("");
  });
});

describe("buildRemotePluginPage", () => {
  const files: Record<string, [string, string]> = {
    "/plugin-ui/account/ui/index.html": [
      "text/html",
      '<!doctype html><html><head><script>window.__hostInjected = 1;</script>' +
        '<script src="../../_kit/ducky.js"></script><link rel="stylesheet" href="account.css">' +
        '</head><body><img src="logo.png"><script type="application/json" id="cfg">{"a":1}</script>' +
        '<script src="account.js" defer></script></body></html>',
    ],
    "/plugin-ui/_kit/ducky.js": ["text/javascript", "window.ducky = {};"],
    "/plugin-ui/account/ui/account.js": ["text/javascript", "console.log('account');"],
    "/plugin-ui/account/ui/account.css": ["text/css", "@font-face{src:url(fonts/a.woff2)} body{color:red}"],
    "/plugin-ui/account/ui/fonts/a.woff2": ["font/woff2", "WOFF"],
    "/plugin-ui/account/ui/logo.png": ["image/png", "PNG"],
    "/plugin-ui/_kit/ducky.css": ["text/css", ".dk-btn{color:blue}"],
  };

  async function build() {
    const asked: string[] = [];
    const blobs = new Map<string, Blob>();
    const dropped: string[] = [];
    const page = await buildRemotePluginPage(
      "/plugin-ui/account/ui/index.html",
      async (path) => {
        asked.push(path);
        const f = files[path];
        return f ? new Response(f[1], { status: 200, headers: { "Content-Type": f[0] } }) : new Response("", { status: 404 });
      },
      (blob) => {
        const url = `blob:test/${blobs.size}`;
        blobs.set(url, blob);
        return url;
      },
      (url) => dropped.push(url),
    );
    return { page, asked, blobs, dropped };
  }

  it("turns the page into one blob document with every file fetched over the link", async () => {
    const { page, asked, blobs } = await build();
    expect(asked).toEqual(expect.arrayContaining([
      "/plugin-ui/account/ui/index.html", "/plugin-ui/_kit/ducky.js", "/plugin-ui/account/ui/account.js",
      "/plugin-ui/account/ui/account.css", "/plugin-ui/account/ui/fonts/a.woff2", "/plugin-ui/account/ui/logo.png",
    ]));
    expect(asked.some((p) => p.startsWith(SITE_PANEL))).toBe(false);
    const doc = new DOMParser().parseFromString(await blobs.get(page.url)!.text(), "text/html");
    const scripts = Array.from(doc.querySelectorAll("script")).filter((s) => s.type !== "application/json");
    // The site's CSP runs blob: scripts only: nothing inline is left, order kept.
    expect(scripts.map((s) => s.getAttribute("src")?.startsWith("blob:"))).toEqual([true, true, true]);
    expect(scripts.every((s) => !(s.textContent || "").trim())).toBe(true);
    expect(await blobs.get(scripts[0].getAttribute("src")!)!.text()).toBe("window.__hostInjected = 1;");
    expect(await blobs.get(scripts[1].getAttribute("src")!)!.text()).toBe("window.ducky = {};");
    expect(scripts[2].hasAttribute("defer")).toBe(true);
    expect(doc.getElementById("cfg")?.textContent).toBe('{"a":1}'); // data blocks stay as they are
    expect(doc.querySelector("link[rel=stylesheet]")).toBeNull();
    // ducky.js links ducky.css beside its own URL, which a blob: script lacks: it comes inline first.
    const kit = doc.querySelector("style[data-ducky-kit]");
    expect(kit?.textContent).toBe(".dk-btn{color:blue}");
    expect(kit?.nextElementSibling?.getAttribute("src")).toBe(scripts[1].getAttribute("src"));
    const css = doc.querySelector("style:not([data-ducky-kit])")?.textContent || "";
    expect(css).toContain(`url("data:font/woff2;base64,${btoa("WOFF")}")`);
    expect(css).toContain("body{color:red}");
    expect(doc.querySelector("img")?.getAttribute("src")).toMatch(/^blob:/);
  });

  it("revoke drops every blob URL it made", async () => {
    const { page, blobs, dropped } = await build();
    page.revoke();
    expect(dropped.sort()).toEqual([...blobs.keys()].sort());
  });

  it("a page the desktop can't find fails and leaves no blob URLs behind", async () => {
    const dropped: string[] = [];
    await expect(buildRemotePluginPage("/plugin-ui/gone/ui/index.html", async () => new Response("", { status: 404 }),
      () => "blob:x", (u) => dropped.push(u))).rejects.toThrow("404");
    expect(dropped).toEqual([]);
  });
});
