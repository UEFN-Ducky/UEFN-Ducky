import { describe, expect, it } from "vitest";
import { parseRichContent } from "./parseRichContent";

describe("parseRichContent", () => {
  it("returns markdown for plain prose", () => {
    const out = parseRichContent("## Hello\n\n- item one");
    expect(out.kind).toBe("markdown");
    if (out.kind === "markdown") expect(out.text).toContain("Hello");
  });

  it("parses __rich JSON envelope", () => {
    const raw = JSON.stringify({
      __rich: true,
      summary: "Devices",
      blocks: [{ type: "paragraph", text: "Found 3" }],
    });
    const out = parseRichContent(raw);
    expect(out.kind).toBe("blocks");
    if (out.kind === "blocks") {
      expect(out.summary).toBe("Devices");
      expect(out.blocks).toHaveLength(1);
    }
  });

  it("parses ducky-rich fenced block", () => {
    const raw = [
      "Summary text",
      "",
      "```ducky-rich",
      JSON.stringify([{ type: "heading", level: 2, text: "Devices" }]),
      "```",
    ].join("\n");
    const out = parseRichContent(raw);
    expect(out.kind).toBe("blocks");
    if (out.kind === "blocks") {
      expect(out.blocks[0]?.type).toBe("heading");
    }
  });
});
