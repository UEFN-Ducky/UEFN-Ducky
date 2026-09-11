import { describe, expect, it } from "vitest";
import { promoteMarkdownToSegments } from "./promoteMarkdownBlocks";

const MOCK = `# Ledger Test Complete
\`ducky run sync --ledger\`

Leaving \`Props\` unwired — **3 of 4 fields** are wired.

## Run Summary
- **Editor changes:** 23 applied
- **Blocked:** 4 retries
- **Programs:** UEFN 20 · Blender 2 · Verse 1 · File 1

## Inventory — \`Test/LedgerFull\`
- **Verse device** / \`ledger_full_test_device.verse\` — 3 of 4 wired; Props stale.
- **Devices** / \`FullTest_EntryTrigger, FullTest_Button, FullTest_HudMessage\` — wired into the Verse device.

> **Loose end:** Props array on \`FullTest_Manager\` — listener stale reflection.
`;

describe("promoteMarkdownToSegments", () => {
  it("promotes the mock ledger report", () => {
    const segs = promoteMarkdownToSegments(MOCK);
    const types = segs.map((s) => (s.kind === "block" ? s.block.type : "markdown"));
    expect(types).toContain("header");
    expect(types).toContain("stats");
    expect(types).toContain("inventory");
    expect(types).toContain("callout");
    expect(types).toContain("markdown");

    const header = segs.find((s) => s.kind === "block" && s.block.type === "header");
    expect(header?.kind === "block" && header.block.type === "header" && header.block.command).toBe(
      "ducky run sync --ledger",
    );

    const sample = promoteMarkdownToSegments(`## Run Summary
- **Editor changes:** 3 applied
- **Blocked:** 1 retry
- **Programs:** UEFN 2 · Blender 1 · Verse 4 · File 2`);
    const sampleStats = sample.find((s) => s.kind === "block" && s.block.type === "stats");
    expect(sampleStats?.kind === "block" && sampleStats.block.type === "stats" && sampleStats.block.changes).toBe(3);
    expect(sampleStats?.kind === "block" && sampleStats.block.type === "stats" && sampleStats.block.blocked).toBe(1);

    const stats = segs.find((s) => s.kind === "block" && s.block.type === "stats");
    expect(stats?.kind === "block" && stats.block.type === "stats" && stats.block.changes).toBe(23);
    expect(stats?.kind === "block" && stats.block.type === "stats" && stats.block.blocked).toBe(4);
    expect(
      stats?.kind === "block" && stats.block.type === "stats" && stats.block.programs?.uefn,
    ).toBe(20);

    const inv = segs.find((s) => s.kind === "block" && s.block.type === "inventory");
    expect(inv?.kind === "block" && inv.block.type === "inventory" && inv.block.folder).toBe(
      "Test/LedgerFull",
    );
    expect(inv?.kind === "block" && inv.block.type === "inventory" && inv.block.items).toHaveLength(2);
    expect(
      inv?.kind === "block" && inv.block.type === "inventory" && inv.block.items[0]?.kind,
    ).toBe("verse");

    const callout = segs.find((s) => s.kind === "block" && s.block.type === "callout");
    expect(callout?.kind === "block" && callout.block.type === "callout" && callout.block.title).toBe(
      "Loose End Remaining",
    );
  });

  it("promotes labeled Blender/UEFN/Verse counts into the programs widget", () => {
    const segs = promoteMarkdownToSegments(`## Run Summary
- **Blender ops:** 5 (base mesh, legs, seat join, backrest join, material assign)
- **UEFN asset creates:** 4 Blueprints (Prefabs) + 1 Material + 1 Material Instance + 1 Widget Blueprint
- **Verse files:** 2, compiled clean (0 diagnostics via Epic VerseToolset.BuildAll)
- **Level actors placed:** 5, across 4 outliner folders`);
    const stats = segs.find((s) => s.kind === "block" && s.block.type === "stats");
    expect(stats?.kind === "block" && stats.block.type === "stats" && stats.block.programs).toEqual({
      blender: 5,
      uefn: 9,
      verse: 2,
    });
  });

  it("leaves unmatched bullets as markdown", () => {
    const segs = promoteMarkdownToSegments("## Inventory\n- just a normal bullet\n- another one");
    expect(segs.every((s) => s.kind === "markdown")).toBe(true);
    if (segs[0]?.kind === "markdown") {
      expect(segs[0].text).toContain("just a normal bullet");
    }
  });

  it("leaves a one-line answer unadorned", () => {
    const segs = promoteMarkdownToSegments("ok");
    expect(segs).toEqual([{ kind: "markdown", text: "ok" }]);
  });

  it("preserves prose, caveats and tool evidence without fabricating metrics", () => {
    const prose = "Created `SM_Chair` and `SM_Desk` in Blender. Screenshot failed, but `blender_get_scene_info` confirmed the meshes.";
    expect(promoteMarkdownToSegments(prose)).toEqual([{ kind: "markdown", text: prose }]);
  });

  it.each(["NOTE", "TIP", "WARNING", "CAUTION", "ERROR", "SUCCESS"])("renders a %s alert with inline content", (label) => {
    const segments = promoteMarkdownToSegments(`> [!${label}] Custom title\n> **Result** for ` + "`Props`" + "\n> Second line.");
    expect(segments).toEqual([{ kind: "block", block: {
      type: "callout", title: "Custom title", text: "**Result** for `Props`\nSecond line.",
      tone: ["NOTE", "TIP"].includes(label) ? "info" : label === "ERROR" ? "error" : label === "SUCCESS" ? "success" : "warn",
    } }]);
  });

  it("keeps ordinary quotes and unknown alert types intact", () => {
    for (const text of ["> Note that this is quoted prose", "> [!CUSTOM] Hello", "> A normal quote."]) {
      expect(promoteMarkdownToSegments(text)).toEqual([{ kind: "markdown", text }]);
    }
  });

  it.each(["```markdown", "~~~~markdown"])("does not promote examples inside %s fences", (fence) => {
    const text = `${fence}\n${MOCK}\n${fence.replace("markdown", "")}`;
    expect(promoteMarkdownToSegments(text)).toEqual([{ kind: "markdown", text }]);
  });

  it("does not promote an unfinished fenced example", () => {
    const text = "```markdown\n" + MOCK.trim();
    expect(promoteMarkdownToSegments(text)).toEqual([{ kind: "markdown", text }]);
  });

  it("allows a report title without a command chip", () => {
    expect(promoteMarkdownToSegments("# Build complete\n\nDetails.")[0]).toEqual({ kind: "block", block: {
      type: "header", title: "Build complete", command: undefined,
    } });
  });

  it("accepts blank lines between inventory rows and Windows line endings", () => {
    const text = "## Inventory — `Test/Full`\r\n\r\n- **Verse device** / `a.verse` — **Wired**.\r\n\r\n- **Blender mesh** / `SM_Crate` — Placed.";
    const seg = promoteMarkdownToSegments(text)[0];
    expect(seg?.kind === "block" && seg.block.type === "inventory" && seg.block.items).toHaveLength(2);
  });

  it.each([
    "## Run Summary\n- **Editor changes:** 23 applied\n- Check the remaining props.",
    "## Run Summary\n- **Editor changes:** 23 applied, but two were reverted.",
    "## Run Summary\n- **Editor changes:** -1 applied",
    "## Run Summary\n- **Programs:** UEFN 2 · Unknown 8",
    "## Inventory\n- **Devices** / `Trigger` — Wired.\n- A caveat here.\n- **Devices** / `Button` — Pending.",
    "## Inventory\n- **Devices** / `Trigger` — Wired.\n  - Nested caveat.",
    "## Inventory\n- **Devices** / `Trigger` — Wired.\n  Still needs testing.",
    "## Inventory\n- **Devices** / `Trigger` — Wired.\nStill needs testing.",
  ])("preserves all content in an incomplete or mixed report", (text) => {
    expect(promoteMarkdownToSegments(text)).toEqual([{ kind: "markdown", text }]);
  });
});
