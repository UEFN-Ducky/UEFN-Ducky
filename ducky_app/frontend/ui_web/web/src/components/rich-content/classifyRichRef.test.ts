import { describe, expect, it } from "vitest";
import { classifyRichRef } from "./classifyRichRef";

describe("classifyRichRef", () => {
  it("opens a bare Verse file under Content/Verse", () => {
    const r = classifyRichRef("ledger_full_test_device.verse");
    expect(r.kind).toBe("verse");
    expect(r.open).toEqual({ type: "file", path: "Content/Verse/ledger_full_test_device.verse" });
  });

  it("classifies the ledger report chips", () => {
    expect(classifyRichRef("EntryTrigger").kind).toBe("field");
    expect(classifyRichRef("P_LedgerTest_Prefab").kind).toBe("prefab");
    expect(classifyRichRef("/ExampleProject1/Prefabs")).toEqual(
      expect.objectContaining({
        kind: "folder",
        open: { type: "asset", path: "Prefabs" },
      }),
    );
    expect(classifyRichRef("SM_LedgerTestCrate").kind).toBe("mesh");
    expect(classifyRichRef("UW_LedgerTestHud").kind).toBe("umg");
    expect(classifyRichRef("FullTest_EntryTrigger").kind).toBe("actor");
    expect(classifyRichRef("@editable").kind).toBe("keyword");
    expect(classifyRichRef("CanvasPanel").kind).toBe("umg");
    expect(classifyRichRef("trigger_device").kind).toBe("device");
    expect(classifyRichRef("blender_get_scene_info").kind).toBe("tool");
    expect(classifyRichRef("EntryTrigger").open).toBeUndefined();
  });
});
