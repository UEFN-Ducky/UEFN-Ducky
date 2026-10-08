import { describe, expect, it } from "vitest";
import { storageLine, switchConfirm, syncText } from "./scopeBarText";

const GB = 1024 ** 3;

describe("scope bar text", () => {
  it("warns from 80%, says full at the limit, and names the level in text", () => {
    expect(storageLine({ usedBytes: 34 * 1024 ** 2, limitBytes: 5 * GB })).toMatchObject({
      text: "34 MB of 5 GB",
      level: "ok",
      note: "",
    });
    expect(storageLine({ usedBytes: 4 * GB, limitBytes: 5 * GB })).toMatchObject({ level: "warn", note: "80% used" });
    expect(storageLine({ usedBytes: 5 * GB, limitBytes: 5 * GB })).toMatchObject({ level: "full", note: "Storage full" });
    expect(storageLine({ usedBytes: 1 })).toBeNull();
  });

  it("says when it synced, what is queued, and the switch warning", () => {
    expect(syncText({ syncedAt: 100, pending: 0, state: "ok" }, 105_000)).toBe("Synced 5 s ago");
    expect(syncText({ syncedAt: 100, pending: 3, state: "offline" }, 105_000)).toBe("Offline · 3 changes waiting");
    expect(switchConfirm("BrainRot TCG", { kind: "team", label: "Alpha Studio" })).toEqual({
      title: "Switch BrainRot TCG to Alpha Studio data?",
      message: "Nothing is copied. BrainRot TCG restarts with Alpha Studio's data.",
    });
    expect(switchConfirm("BrainRot TCG", { kind: "personal", label: "Local" })).toEqual({
      title: "Switch BrainRot TCG to Local data?",
      message: "Nothing is copied. BrainRot TCG restarts with its Local data.",
    });
  });
});
