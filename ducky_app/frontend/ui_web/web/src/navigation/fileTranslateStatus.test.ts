import { describe, expect, it } from "vitest";

import {
  fileTranslateStatusKey,
  getFileTranslateStatus,
  setFileTranslateStatus,
  subscribeFileTranslateStatus,
} from "./fileTranslateStatus";

describe("fileTranslateStatus", () => {
  it("keys path+lang case-insensitively", () => {
    expect(fileTranslateStatusKey("Verse/A.verse", "Bulgarian")).toBe(
      fileTranslateStatusKey("verse/a.verse", "bulgarian"),
    );
  });

  it("notifies subscribers and stores translating / cached", () => {
    let ticks = 0;
    const unsub = subscribeFileTranslateStatus(() => {
      ticks += 1;
    });
    setFileTranslateStatus("Verse/x.verse", "bg", "translating", "Translating…");
    expect(getFileTranslateStatus("Verse/x.verse", "bg").phase).toBe("translating");
    expect(ticks).toBeGreaterThan(0);
    setFileTranslateStatus("Verse/x.verse", "bg", "cached", "Cached");
    expect(getFileTranslateStatus("Verse/x.verse", "bg").label).toBe("Cached");
    setFileTranslateStatus("Verse/x.verse", "bg", "idle");
    expect(getFileTranslateStatus("Verse/x.verse", "bg").phase).toBe("idle");
    unsub();
  });
});
