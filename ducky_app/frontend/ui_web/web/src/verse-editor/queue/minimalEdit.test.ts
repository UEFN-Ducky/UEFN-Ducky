import { describe, expect, it } from "vitest";

import { minimalEdit } from "./minimalEdit";

const apply = (before: string, after: string): string => {
  const e = minimalEdit(before, after);
  return e ? before.slice(0, e.start) + e.text + before.slice(e.end) : before;
};

describe("minimalEdit", () => {
  const cases: [string, string, string][] = [
    ["insert", "a\nb\nc", "a\nb\nNEW\nc"],
    ["delete", "a\nb\nc\nd", "a\nd"],
    ["replace", "x = 1\ny = 2\n", "x = 1\ny = 3\n"],
    ["empty before", "", "all new"],
    ["empty after", "all gone", ""],
    ["repeated chars", "aaaa", "aaaaaa"],
    ["crlf", "a\r\nb\r\n", "a\r\nX\r\nb\r\n"],
    ["surrogates", "x😀y", "x😃y"],
  ];
  it.each(cases)("%s round-trips", (_n, before, after) => {
    expect(apply(before, after)).toBe(after);
  });

  it("touches only the changed span of a big file", () => {
    const lines = Array.from({ length: 3000 }, (_, i) => `line ${i}`);
    const before = lines.join("\n");
    lines[1500] = "changed";
    const e = minimalEdit(before, lines.join("\n"))!;
    expect(e.text).toBe("changed");
    expect(before.slice(e.start, e.end)).toBe("line 1500");
  });

  it("never splits a surrogate pair or CRLF", () => {
    const e = minimalEdit("x😀y", "x😃y")!;
    expect(e.text).toBe("😃");
    const c = minimalEdit("a\r\nb", "a\nb")!;
    expect(c.start).toBeLessThanOrEqual(1);
  });

  it("is null when nothing changed", () => {
    expect(minimalEdit("same", "same")).toBeNull();
  });
});
