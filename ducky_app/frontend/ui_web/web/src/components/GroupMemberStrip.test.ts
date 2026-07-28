import { describe, expect, it } from "vitest";

import { shortModelLabel, shortWhenToUse } from "./GroupMemberStrip";

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

describe("shortModelLabel", () => {
  it("shows model id from qualified selection", () => {
    expect(shortModelLabel("anthropic:claude-sonnet-4-5")).toBe("claude-sonnet-4-5");
  });

  it("falls back for empty", () => {
    expect(shortModelLabel("")).toBe("Default model");
  });
});
