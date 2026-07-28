import { describe, expect, it } from "vitest";

import { clampSpeed, formatSpeed, snapSpeed, SPEED_OPTIONS } from "./voiceSettings";

describe("voice speed helpers", () => {
  it("clamps to 0.25–4", () => {
    expect(clampSpeed(0)).toBe(1);
    expect(clampSpeed(-1)).toBe(1);
    expect(clampSpeed(0.1)).toBe(0.25);
    expect(clampSpeed(5)).toBe(4);
    expect(clampSpeed(1.5)).toBe(1.5);
  });

  it("snaps to two decimals", () => {
    expect(snapSpeed(1.234)).toBe(1.23);
    expect(snapSpeed(3.999)).toBe(4);
  });

  it("formats short labels", () => {
    expect(formatSpeed(1)).toBe("1×");
    expect(formatSpeed(1.5)).toBe("1.5×");
    expect(formatSpeed(1.25)).toBe("1.25×");
  });

  it("includes 4× in presets", () => {
    expect(SPEED_OPTIONS.some((o) => o.value === "4")).toBe(true);
    expect(SPEED_OPTIONS.some((o) => o.value === "2")).toBe(true);
  });
});
