// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { FinishTeamToolView } from "./FinishTeamCard";
import { resolveToolPresenterBlocks, resolveToolPresenterView } from "./resolveToolPresenter";

afterEach(cleanup);

describe("ducky_finish_team view", () => {
  const result = JSON.stringify({
    ok: true, ui: "finish_team", status: "needs_confirm", plan_title: "Swarm speed",
    seats: [
      { conv_id: "c1", name: "U1 Card shell", commits: ["c66cfb9e", "aaf1583c"], dirty: [] },
      { conv_id: "c2", name: "U2 Tree", commits: ["a4428d5d"], dirty: [] },
    ],
  });

  it("renders the Finish team card, prefixed or not; other results fall through", () => {
    const view = resolveToolPresenterView("mcp__uefn__ducky_finish_team", result);
    expect(view).toBe(FinishTeamToolView);
    expect(resolveToolPresenterView("ducky_finish_team", JSON.stringify({ ok: false, error: "no team" }))).toBeNull();
    render(createElement(view!, { toolName: "ducky_finish_team", arguments: {}, resultText: result, isSuccess: true }));
    expect(screen.getByText("Finish team: 2 seats · 3 commits · 0 uncommitted")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Archive team" })).toBeTruthy();
  });
});

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

  it("on a Mac shows where Verse is checked, with a button per PC, never 'no errors'", () => {
    const raw = JSON.stringify({
      ok: true,
      lsp: "remote",
      files: [],
      complete: false,
      open_remote: [{ key_id: "pc1", name: "Tas's PC" }, { key_id: "pc2", name: "Studio PC" }],
      note: "Verse diagnostics run on your PC: open its Ducky (Duckies → your PC) and check or build Verse there.",
    });
    const blocks = resolveToolPresenterBlocks({
      toolName: "workspace_list_verse_errors",
      arguments: {},
      resultText: raw,
      isSuccess: true,
    });
    expect(blocks?.[0]).toMatchObject({ type: "callout", tone: "info" });
    expect((blocks?.[0] as { text: string }).text).toContain("Verse diagnostics run on your PC");
    expect(blocks?.filter((b) => b.type === "ducky_link")).toEqual([
      { type: "ducky_link", key_id: "pc1", name: "Tas's PC" },
      { type: "ducky_link", key_id: "pc2", name: "Studio PC" },
    ]);
    expect(JSON.stringify(blocks)).not.toContain("No Verse errors");
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

  it("shows a plain-text read's file text without its header line", () => {
    const blocks = resolveToolPresenterBlocks({
      toolName: "workspace_read_file",
      arguments: { relative_path: "Verse/Foo.verse" },
      resultText: "Verse/Foo.verse  lines 1-2 of 2\nusing { /Verse.org/Verse }\nfoo := 1",
      isSuccess: true,
    });
    const code = blocks?.find((b) => b.type === "code") as { text?: string } | undefined;
    expect(code?.text).toBe("using { /Verse.org/Verse }\nfoo := 1");
  });
});
