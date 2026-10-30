// The phone panel is served from a folder of the site (/static/plugins/uefn-ducky/panel/),
// so a picture under public/ written as "/x.svg" hits the site root and 404s (the
// broken header icons). Vite rewrites CSS url()s, not strings in code: keep them "./x".
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname);
const ROOT_PUBLIC = /["'`]\/(verse-workflow\/|verse-icon|[A-Za-z]+MCPIcon|favicon|draco\/|unreal-icon|duckies\/(?!custom\/))/;

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return files(p);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [p] : [];
  });
}

describe("pictures from public/", () => {
  it("are relative paths, so they resolve in the phone panel's folder", () => {
    const bad: string[] = [];
    for (const f of files(SRC)) {
      const rel = relative(SRC, f).replace(/\\/g, "/");
      // The desktop's blob allowlist names its own served paths; it is not a picture URL.
      if (rel === "remote/protocol.ts") continue;
      readFileSync(f, "utf8")
        .split("\n")
        .forEach((line, i) => {
          if (ROOT_PUBLIC.test(line)) bad.push(`${rel}:${i + 1}: ${line.trim()}`);
        });
    }
    expect(bad).toEqual([]);
  });
});
