import { describe, expect, it } from "vitest";

import { resampleLinear } from "./transcriptionSession";

describe("resampleLinear", () => {
  it("returns the same buffer when rates match", () => {
    const input = new Float32Array([0, 0.5, 1, 0.5, 0]);
    const out = resampleLinear(input, 24000, 24000);
    expect(out).toBe(input);
  });

  it("halves length when downsampling 48k → 24k", () => {
    const input = new Float32Array(480);
    for (let i = 0; i < input.length; i += 1) input[i] = i / (input.length - 1);
    const out = resampleLinear(input, 48000, 24000);
    expect(out.length).toBe(240);
    expect(out[0]).toBeCloseTo(0, 5);
    // Last sample is interpolated near the end (not necessarily exact 1).
    expect(out[out.length - 1]!).toBeCloseTo(1, 2);
  });

  it("preserves endpoints on upsample", () => {
    const input = new Float32Array([-1, 1]);
    const out = resampleLinear(input, 12000, 24000);
    expect(out.length).toBe(4);
    expect(out[0]).toBeCloseTo(-1, 5);
    expect(out[out.length - 1]!).toBeCloseTo(1, 5);
  });
});
