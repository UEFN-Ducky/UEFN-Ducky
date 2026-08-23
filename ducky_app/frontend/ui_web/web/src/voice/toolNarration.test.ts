import { describe, expect, it } from "vitest";

import { needsLlmSummary, spokenToolResult, spokenToolStart } from "./toolNarration";

describe("toolNarration", () => {
  it("builds a start line for write-file with a path", () => {
    expect(
      spokenToolStart({
        name: "workspace_write_file",
        arguments: { path: "Verse/NPCCore/prey.verse" },
      }),
    ).toBe("Writing the file prey dot verse.");
  });

  it("falls back to the human label when the tool is unknown", () => {
    expect(spokenToolStart({ name: "custom_thing", arguments: {} })).toBe("Custom thing.");
  });

  it("digests a successful write", () => {
    expect(
      spokenToolResult({
        name: "workspace_write_file",
        arguments: { path: "Verse/prey.verse" },
        status: "success",
        result: JSON.stringify({ ok: true, path: "Verse/prey.verse", lines: 240 }),
      }),
    ).toBe("Wrote prey dot verse, 240 lines.");
  });

  it("digests an error", () => {
    expect(
      spokenToolResult({
        name: "workspace_read_file",
        arguments: { path: "missing.verse" },
        status: "error",
        result: "file not found",
      }),
    ).toBe("Failed: file not found");
  });

  it("digests a count-shaped result", () => {
    expect(
      spokenToolResult({
        name: "search_assets",
        arguments: { search: "cat" },
        status: "success",
        result: JSON.stringify({ assets: [{}, {}, {}] }),
      }),
    ).toBe("Found 3 results.");
  });

  it("asks for an LLM summary on opaque JSON", () => {
    expect(
      needsLlmSummary({
        name: "get_verse_api",
        arguments: {},
        status: "success",
        result: JSON.stringify({ class: "animation_controller", methods: { play: {}, stop: {} } }),
      }),
    ).toBe(true);
    expect(
      needsLlmSummary({
        name: "workspace_write_file",
        arguments: { path: "a.verse" },
        status: "success",
        result: JSON.stringify({ ok: true, path: "a.verse" }),
      }),
    ).toBe(false);
  });
});
