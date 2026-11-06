import { describe, expect, it } from "vitest";
import { stableJson } from "./stableJson";

describe("stableJson", () => {
  it("matches values that differ only in key order (the launcher used to sort keys)", () => {
    const sent = { foundation: { text: "#fff", bg: "#000" }, overrides: {}, active_profile_id: "p", sounds: { volume: 0.5, enabled: false } };
    const echoed = JSON.parse(JSON.stringify({ active_profile_id: "p", foundation: { bg: "#000", text: "#fff" }, overrides: {}, sounds: { enabled: false, volume: 0.5 } }));
    expect(JSON.stringify(sent)).not.toBe(JSON.stringify(echoed));
    expect(stableJson(sent)).toBe(stableJson(echoed));
  });

  it("still tells real changes and array order apart", () => {
    expect(stableJson({ a: 1 })).not.toBe(stableJson({ a: 2 }));
    expect(stableJson([1, 2])).not.toBe(stableJson([2, 1]));
    expect(stableJson({ a: undefined, b: null })).toBe('{"b":null}');
  });
});
