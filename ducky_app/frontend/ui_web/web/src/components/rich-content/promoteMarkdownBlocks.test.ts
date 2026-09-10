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
});
