import { describe, expect, it } from "vitest";

import { roundTripMarkdown } from "./markdown";

describe("roundTripMarkdown", () => {
  it("preserves headings, lists, and code fences", () => {
    const src = [
      "# Title",
      "",
      "## Section",
      "",
      "A paragraph with **bold**.",
      "",
      "- alpha",
      "- beta",
      "",
      "1. one",
      "2. two",
      "",
      "```",
      "const x = 1;",
      "```",
      "",
    ].join("\n");

    const out = roundTripMarkdown(src);
    expect(out).toContain("# Title");
    expect(out).toContain("## Section");
    expect(out).toMatch(/alpha/);
    expect(out).toMatch(/beta/);
    expect(out).toMatch(/one/);
    expect(out).toMatch(/two/);
    expect(out).toMatch(/const x = 1/);
    expect(out).toMatch(/\*\*bold\*\*|__bold__/);
  });
});
