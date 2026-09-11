import { describe, expect, it } from "vitest";
import { framedHistType } from "./framedHist";

describe("framedHistType", () => {
  it("replaces the landing entry, then pushes", () => {
    expect(framedHistType(1)).toBe("ud-replace");
    expect(framedHistType(2)).toBe("ud-push");
  });
});
