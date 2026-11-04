// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * An infinite animation of `content` (or of layout like width/height/top/left) runs on the
 * page thread: the window recalculated style every frame for as long as an agent ran
 * (Nov 2026, ~100 recalcs/s at 10 agents). Infinite animations may move only transform,
 * opacity and the like, which the compositor runs.
 */
const SRC = join(__dirname, "..", "..");
const PAGE_THREAD = /(^|[;{\s])(content|width|height|top|left|right|bottom|margin[\w-]*|padding[\w-]*)\s*:/;

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "node_modules" ? [] : cssFiles(path);
    return name.endsWith(".css") ? [path] : [];
  });
}

/** keyframe name -> its body, and the names some rule runs forever. */
function scan(css: string): { frames: Map<string, string>; infinite: Set<string> } {
  const frames = new Map<string, string>();
  const re = /@keyframes\s+([\w-]+)\s*\{/g;
  for (let m = re.exec(css); m; m = re.exec(css)) {
    let depth = 1;
    let i = re.lastIndex;
    while (i < css.length && depth > 0) depth += css[i] === "{" ? 1 : css[i] === "}" ? -1 : 0, i += 1;
    frames.set(m[1], css.slice(re.lastIndex, i - 1));
  }
  const infinite = new Set<string>();
  for (const decl of css.match(/animation\s*:[^;}]*infinite[^;}]*/g) ?? []) {
    for (const word of decl.replace(/^animation\s*:/, "").split(/[\s,]+/)) infinite.add(word);
  }
  return { frames, infinite };
}

describe("infinite CSS animations", () => {
  it("never animate content or layout (the page thread)", () => {
    const frames = new Map<string, { body: string; file: string }>();
    const infinite = new Set<string>();
    for (const file of cssFiles(SRC)) {
      const found = scan(readFileSync(file, "utf-8"));
      for (const [name, body] of found.frames) frames.set(name, { body, file });
      for (const name of found.infinite) infinite.add(name);
    }
    const bad = [...infinite]
      .filter((name) => frames.has(name) && PAGE_THREAD.test(frames.get(name)!.body.replace(/\/\*[\s\S]*?\*\//g, "")))
      .map((name) => `${name} (${frames.get(name)!.file.slice(SRC.length + 1)})`);
    expect(bad).toEqual([]);
  });

  it("the guard sees a content keyframe", () => {
    const { frames, infinite } = scan("a::after{animation: dots 1s steps(4) infinite}@keyframes dots{0%{content:''}100%{content:'...'}}");
    expect(infinite.has("dots") && PAGE_THREAD.test(frames.get("dots")!)).toBe(true);
  });
});
