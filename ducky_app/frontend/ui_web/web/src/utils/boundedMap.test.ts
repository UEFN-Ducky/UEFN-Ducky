import { describe, expect, it } from "vitest";
import { boundedGet, boundedSet } from "./boundedMap";

describe("boundedSet", () => {
  it("evicts the oldest key once the cap is reached", () => {
    const m = new Map<string, number>();
    for (let i = 0; i < 5; i += 1) boundedSet(m, `k${i}`, i, 3);
    expect(m.size).toBe(3);
    expect([...m.keys()]).toEqual(["k2", "k3", "k4"]);
  });

  it("re-inserting a key refreshes it instead of growing", () => {
    const m = new Map<string, number>();
    boundedSet(m, "a", 1, 2);
    boundedSet(m, "b", 2, 2);
    boundedSet(m, "a", 9, 2); // a is now the newest
    boundedSet(m, "c", 3, 2); // evicts b, not a
    expect(m.size).toBe(2);
    expect([...m.keys()]).toEqual(["a", "c"]);
    expect(m.get("a")).toBe(9);
  });

  it("holds at the cap no matter how many writes arrive", () => {
    const m = new Map<number, number>();
    for (let i = 0; i < 10_000; i += 1) boundedSet(m, i, i, 24);
    expect(m.size).toBe(24);
  });

  it("a cap of one keeps only the newest", () => {
    const m = new Map<string, number>();
    boundedSet(m, "a", 1, 1);
    boundedSet(m, "b", 2, 1);
    expect([...m.entries()]).toEqual([["b", 2]]);
  });
});

describe("boundedGet", () => {
  it("returns the value and makes it the most recent", () => {
    const m = new Map<string, number>();
    boundedSet(m, "a", 1, 2);
    boundedSet(m, "b", 2, 2);
    expect(boundedGet(m, "a")).toBe(1);
    boundedSet(m, "c", 3, 2); // "a" was just read, so "b" is the oldest
    expect([...m.keys()]).toEqual(["a", "c"]);
  });

  it("is undefined for a missing key and does not create one", () => {
    const m = new Map<string, number>();
    expect(boundedGet(m, "nope")).toBeUndefined();
    expect(m.size).toBe(0);
  });
});
