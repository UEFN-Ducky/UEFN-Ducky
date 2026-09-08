import { describe, expect, it } from "vitest";

import cases from "../../../../../backend/workspace/schemas/fixtures/lane_glob_cases.json";
import {
  LaneGlobError,
  checkLaneSet,
  match,
  normalizeGlob,
  normalizeLane,
  overlap,
  parseLaneText,
  shortLaneLabel,
  validateLaneGlobs,
} from "./laneGlob";

type NormalizeCase = { input: string; output: string };
type MatchCase = { pattern: string; path: string; expected: boolean };
type OverlapCase = { a: string; b: string; expected: string | null };

describe("laneGlob — shared contract cases", () => {
  it.each((cases.normalize as NormalizeCase[]).map((c) => [c.input, c.output]))(
    "normalize %j → %j",
    (input, output) => {
      expect(normalizeGlob(input)).toBe(output);
    },
  );

  it.each(cases.invalid as string[])("rejects %j", (pattern) => {
    expect(() => normalizeGlob(pattern)).toThrow(LaneGlobError);
  });

  it.each((cases.match as MatchCase[]).map((c) => [c.pattern, c.path, c.expected]))(
    "match(%j, %j) = %j",
    (pattern, path, expected) => {
      expect(match(pattern, path)).toBe(expected);
    },
  );

  it.each((cases.overlap as OverlapCase[]).map((c) => [c.a, c.b, c.expected]))(
    "overlap(%j, %j) = %j",
    (a, b, expected) => {
      expect(overlap(a, b)).toBe(expected);
      expect(overlap(b, a)).toBe(expected);
    },
  );
});

describe("laneGlob — editor helpers", () => {
  it("normalizeLane accepts text and lists", () => {
    expect(normalizeLane(null)).toBeNull();
    expect(normalizeLane([])).toEqual([]);
    expect(normalizeLane("Content/Verse/Hub\nContent/Verse/Shop/**, Content/Verse/Hub")).toEqual([
      "Content/Verse/Hub/**",
      "Content/Verse/Shop/**",
    ]);
  });

  it("checkLaneSet reports errors and warnings per pair", () => {
    const verdict = checkLaneSet({
      hacker: ["Content/Verse/Shop/**"],
      artist: ["Content/Verse/**"],
      reviewer: [],
      architect: null,
      wild: ["Content/*/Shop/**"],
    });
    expect(verdict.errors).toEqual(["hacker: Content/Verse/Shop/** overlaps artist: Content/Verse/** (contains)"]);
    expect(verdict.warnings.some((w) => w.includes("(maybe)"))).toBe(true);
  });

  it("parseLaneText and validateLaneGlobs", () => {
    expect(parseLaneText(" a/b \n\n c/d, e.verse ")).toEqual(["a/b", "c/d", "e.verse"]);
    expect(validateLaneGlobs(["Content/Verse", "../x"])).toHaveLength(1);
    expect(validateLaneGlobs([])).toEqual([]);
  });

  it("shortLaneLabel", () => {
    expect(shortLaneLabel(null)).toBe("No lane");
    expect(shortLaneLabel([])).toBe("Read-only");
    expect(shortLaneLabel(["Content/Verse/Shop/**"])).toBe("Verse/Shop/");
    expect(shortLaneLabel(["Content/Verse/Shop/**", "Content/Verse/x.verse"])).toBe("Verse/Shop/ +1");
  });
});
