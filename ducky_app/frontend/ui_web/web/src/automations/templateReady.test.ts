import { describe, expect, it } from "vitest";

function canCreate(template: { ready?: boolean } | null): boolean {
  return !template || template.ready !== false;
}

describe("pipeline template lock", () => {
  it("blocks create when ready is false", () => {
    expect(canCreate({ ready: false })).toBe(false);
    expect(canCreate({ ready: true })).toBe(true);
    expect(canCreate(null)).toBe(true);
  });
});
