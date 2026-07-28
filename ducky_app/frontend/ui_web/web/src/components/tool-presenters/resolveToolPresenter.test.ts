import { describe, expect, it } from "vitest";
import { resolveToolPresenterBlocks } from "./resolveToolPresenter";

describe("resolveToolPresenterBlocks", () => {
  it("returns device table for find_devices", () => {
    const raw = JSON.stringify({
      ok: true,
      data: [{ label: "RR_A", kind: "verse_script", class: "Foo" }],
    });
    const blocks = resolveToolPresenterBlocks({
      toolName: "find_devices",
      arguments: {},
      resultText: raw,
      isSuccess: true,
    });
    expect(blocks?.some((b) => b.type === "table")).toBe(true);
  });

  it("returns null for unknown tools", () => {
    const blocks = resolveToolPresenterBlocks({
      toolName: "ducky_ping",
      arguments: {},
      resultText: JSON.stringify({ ok: true, data: { online: true } }),
      isSuccess: true,
    });
    expect(blocks).toBeNull();
  });

  it("shows verse errors from the raw files/items shape (not a false 'no errors')", () => {
    // Real workspace_list_verse_errors shape: per-file dicts with nested items.
    // Regression: this used to render "No Verse errors found." over 30 real errors.
    const raw = JSON.stringify({
      ok: true,
      data: {
        files: [
          {
            path: "Verse/playermanager.verse",
            errors: 2,
            warnings: 0,
            items: [
              { line: 44, column: 1, message: "Unknown identifier `player_connected_to_game`.", severity: "error" },
              { line: 47, column: 1, message: "Unknown identifier `player_connected_to_game`.", severity: "error" },
            ],
          },
        ],
        stale_count: 0,
        from_cache: true,
      },
    });
    const blocks = resolveToolPresenterBlocks({
      toolName: "workspace_list_verse_errors",
      arguments: {},
      resultText: raw,
      isSuccess: true,
    });
    const callout = blocks?.find((b) => b.type === "callout") as { tone?: string; text?: string } | undefined;
    expect(callout?.tone).toBe("error");
    expect(callout?.text).toContain("2");
    expect(blocks?.some((b) => b.type === "table")).toBe(true);
  });

  it("keeps the success callout when all scanned files are clean", () => {
    const raw = JSON.stringify({
      ok: true,
      data: { files: [{ path: "Verse/ok.verse", errors: 0, warnings: 0, items: [] }], stale_count: 0, from_cache: true },
    });
    const blocks = resolveToolPresenterBlocks({
      toolName: "workspace_list_verse_errors",
      arguments: {},
      resultText: raw,
      isSuccess: true,
    });
    const callout = blocks?.find((b) => b.type === "callout") as { tone?: string } | undefined;
    expect(callout?.tone).toBe("success");
  });

  it("formats workspace_read_file with file link and code", () => {
    const raw = JSON.stringify({ ok: true, data: { content: "using { /Verse.org/Verse }\n" } });
    const blocks = resolveToolPresenterBlocks({
      toolName: "workspace_read_file",
      arguments: { relative_path: "Verse/Foo.verse" },
      resultText: raw,
      isSuccess: true,
    });
    expect(blocks?.some((b) => b.type === "file_link")).toBe(true);
    expect(blocks?.some((b) => b.type === "code")).toBe(true);
  });
});
