import { describe, expect, it } from "vitest";
import { tokenizePython } from "./highlightPython";

describe("tokenizePython", () => {
  it("colors keywords, strings, comments, and numbers", () => {
    const code = [
      "import unreal",
      "# count trees",
      'print("hi", 226)',
      "if c > 3:",
      "    pass",
    ].join("\n");
    const kinds = tokenizePython(code).map((t) => `${t.kind}:${t.text}`);
    expect(kinds).toContain("keyword:import");
    expect(kinds).toContain("comment:# count trees");
    expect(kinds).toContain("builtin:print");
    expect(kinds).toContain('string:"hi"');
    expect(kinds).toContain("number:226");
    expect(kinds).toContain("keyword:if");
    expect(kinds).toContain("keyword:pass");
  });

  it("handles f-strings and triples", () => {
    const tokens = tokenizePython(`f"x={n}"\n'''doc'''`);
    expect(tokens.some((t) => t.kind === "string" && t.text.startsWith("f\""))).toBe(true);
    expect(tokens.some((t) => t.kind === "string" && t.text.includes("doc"))).toBe(true);
  });
});
