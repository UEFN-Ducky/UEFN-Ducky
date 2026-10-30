// public/sw.js decides which panel requests go to the PC over WebRTC. Pictures that
// ship with the panel must come from the site instead: over the blob channel they wait
// for the link and were never cached (slow or broken avatars on the phone).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { blobPathAllowed } from "./protocol";

const sw = readFileSync(join(__dirname, "../../public/sw.js"), "utf8");
const prefixes = JSON.parse(
  (/const PREFIXES = (\[[^\]]*\])/.exec(sw)?.[1] ?? "[]").replace(/,\s*\]/, "]"),
) as string[];
const toDesktop = (rel: string) => prefixes.some((p) => rel.startsWith(p));

describe("the panel's service worker", () => {
  it("leaves built-in duckies to the site and sends uploaded ones to the PC", () => {
    expect(toDesktop("duckies/Artist.png")).toBe(false);
    expect(toDesktop("duckies/custom/my-duck.png")).toBe(true);
    expect(toDesktop("plugin-ui/forge/icon.png")).toBe(true);
    expect(toDesktop("assets/index-abc.js")).toBe(false);
    expect(toDesktop("verse-workflow/verse-icon-build.svg")).toBe(false);
  });

  it("only forwards paths the desktop will serve", () => {
    expect(prefixes.length).toBeGreaterThan(0);
    for (const p of prefixes) expect(blobPathAllowed(`/${p}x`)).toBe(true);
  });
});
