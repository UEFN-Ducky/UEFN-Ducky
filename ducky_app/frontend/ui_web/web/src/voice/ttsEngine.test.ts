import { describe, expect, it } from "vitest";

import { chunkOffsets } from "./ttsEngine";

describe("ttsEngine chunkOffsets", () => {
  it("assigns cumulative offsets with space between chunks", () => {
    expect(chunkOffsets(["Hello.", "World."])).toEqual([0, 7]);
    expect(chunkOffsets(["Only"])).toEqual([0]);
    expect(chunkOffsets([])).toEqual([]);
  });
});
