import { describe, expect, it } from "vitest";

import { diffFields, formatValue } from "./JsonDiffView";

describe("diffFields", () => {
  it("marks only the properties that actually differ", () => {
    const rows = diffFields(
      { location: [0, 0, 0], rotation: [0, 0, 0] },
      { location: [0, 0, 250], rotation: [0, 0, 0] },
    );
    expect(rows.map((r) => [r.key, r.changed])).toEqual([
      ["location", true],
      ["rotation", false],
    ]);
    expect(rows[0].before).toBe("[0, 0, 0]");
    expect(rows[0].after).toBe("[0, 0, 250]");
  });

  it("shows a property that only one side has", () => {
    const [row] = diffFields({}, { folder: "Props" });
    expect(row).toMatchObject({ key: "folder", before: "—", after: "Props", changed: true });
  });

  it("gives up on non-objects so the raw view takes over", () => {
    expect(diffFields(undefined, undefined)).toEqual([]);
    expect(diffFields([1, 2], [1, 3])).toEqual([]);
  });
});

describe("formatValue", () => {
  it("keeps values whole rather than truncating them", () => {
    expect(formatValue(1234.5678)).toBe("1234.5678");
    expect(formatValue(null)).toBe("null");
    expect(formatValue(undefined)).toBe("—");
    expect(formatValue({ a: 1 })).toBe('{"a":1}');
  });
});
