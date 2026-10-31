import { describe, expect, it } from "vitest";
import { formatAgoShort } from "./formatRelativeTime";

describe("formatAgoShort", () => {
  const now = 1_800_000_000_000;
  it("labels minutes, hours and days", () => {
    expect(formatAgoShort(undefined, now)).toBe("");
    expect(formatAgoShort(now - 30_000, now)).toBe("now");
    expect(formatAgoShort(now - 60_000, now)).toBe("1m");
    expect(formatAgoShort(now - 20 * 60_000, now)).toBe("20m");
    expect(formatAgoShort(now - 60 * 60_000, now)).toBe("1h");
    expect(formatAgoShort(now - 3 * 86_400_000, now)).toBe("3d");
    expect(formatAgoShort((now - 5 * 60_000) / 1000, now)).toBe("5m");
  });
});
