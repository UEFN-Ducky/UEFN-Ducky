import { describe, expect, it } from "vitest";
import { lostText, storageLine, switchConfirm, syncText } from "./scopeBarText";

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

  it("says a lost team's copy is locked and when it leaves this PC", () => {
    const at = new Date(2026, 9, 15, 12).getTime() / 1000;
    expect(lostText("Alpha Studio", at, "en-US")).toBe(
      "You no longer have access to Alpha Studio. Its data here is locked and leaves this PC on October 15 unless access comes back.",
    );
    expect(lostText("Alpha Studio")).toBe("You no longer have access to Alpha Studio. Its data here is locked until access comes back.");
  });
});
