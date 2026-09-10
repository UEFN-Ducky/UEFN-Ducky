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

  it("recovers the 3D Modeler blender prose into blocks", () => {
    const prose =
      "Created `SM_Chair` and `SM_Desk` in Blender (default cube removed, each object organized into its own collection under `COL_Props`), giving you real scene changes to exercise the ledger/changeset tracking with. The viewport screenshot tool errored on a schema bug on its end, but `blender_get_scene_info` confirms both meshes exist and replaced the default cube.";
    const segs = promoteMarkdownToSegments(prose);
    const types = segs.map((s) => (s.kind === "block" ? s.block.type : "markdown"));
    expect(types).toEqual(["header", "heading", "stats", "inventory", "callout"]);

    const header = segs.find((s) => s.kind === "block" && s.block.type === "header");
    expect(header?.kind === "block" && header.block.type === "header" && header.block.command).toBe(
      "blender",
    );
    expect(
      header?.kind === "block" && header.block.type === "header" && header.block.title,
    ).toBe("Created SM_Chair and SM_Desk in Blender");

    const inv = segs.find((s) => s.kind === "block" && s.block.type === "inventory");
    expect(inv?.kind === "block" && inv.block.type === "inventory" && inv.block.folder).toBe(
      "COL_Props",
    );
    expect(
      inv?.kind === "block" && inv.block.type === "inventory" && inv.block.items.map((i) => i.title),
    ).toEqual(["SM_Chair", "SM_Desk"]);
    expect(
      inv?.kind === "block" && inv.block.type === "inventory" && inv.block.items[0]?.kind,
    ).toBe("blender");

    const callout = segs.find((s) => s.kind === "block" && s.block.type === "callout");
    expect(callout?.kind === "block" && callout.block.type === "callout" && callout.block.text).toMatch(
      /screenshot tool errored/i,
    );
    expect(
      JSON.stringify(segs).includes("blender_get_scene_info"),
    ).toBe(false);
  });
});
