import { describe, expect, it } from "vitest";

import { laneTitle, shortLaneLabel, shortModelLabel, shortWhenToUse } from "./GroupMemberStrip";

describe("shortWhenToUse", () => {
  it("keeps short blurbs", () => {
    expect(shortWhenToUse("Mesh and modeling work")).toBe("Mesh and modeling work");
  });

  it("truncates to ten words", () => {
    const long =
      "Mesh and modeling work — building or importing Static Meshes, merging pieces, UVs textures";
    expect(shortWhenToUse(long, 10)).toBe(
      "Mesh and modeling work — building or importing Static Meshes,…",
    );
  });
});

describe("lane badge", () => {
  it("labels the three lane states", () => {
    expect(shortLaneLabel(null)).toBe("No lane");
    expect(shortLaneLabel([])).toBe("Read-only");
    expect(shortLaneLabel(["Content/Verse/Shop/**"])).toBe("Verse/Shop/");
  });

  it("tooltip lists every glob", () => {
    expect(laneTitle(["a/**", "b.verse"])).toBe("Write lane:\na/**\nb.verse");
    expect(laneTitle([])).toContain("Read-only");
    expect(laneTitle(null)).toContain("No write lane");
  });
});

describe("shortModelLabel", () => {
  it("shows model id from qualified selection", () => {
    expect(shortModelLabel("anthropic:claude-sonnet-4-5")).toBe("claude-sonnet-4-5");
  });

  it("falls back for empty", () => {
    expect(shortModelLabel("")).toBe("Default model");
  });
});
