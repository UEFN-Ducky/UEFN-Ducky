import { describe, expect, it } from "vitest";

import { appendLiveUtterance } from "./liveUtterance";

describe("appendLiveUtterance", () => {
  it("joins segments with a space", () => {
    expect(appendLiveUtterance("hello there", "and more")).toBe("hello there and more");
  });

  it("ignores empty next", () => {
    expect(appendLiveUtterance("kept", "  ")).toBe("kept");
  });

  it("uses next when base is empty", () => {
    expect(appendLiveUtterance("", "first")).toBe("first");
  });
});
