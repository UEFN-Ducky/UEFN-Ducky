import { describe, expect, it } from "vitest";

import {
  diffFields,
  editorStory,
  formatValue,
  isEditorEnvelope,
  isStructuredText,
  opaqueDetail,
  prettyCode,
  projectEditorDiff,
} from "./JsonDiffView";

describe("diffFields", () => {
  it("marks only the properties that actually differ", () => {
    const rows = diffFields(
      { location: [0, 0, 0], rotation: [0, 0, 0] },
      { location: [0, 0, 250], rotation: [0, 0, 0] },
    );
    expect(rows.map((r) => [r.key, r.changed])).toEqual([
      ["location", true],
      ["rotation", false],
    ]);
    expect(rows[0].before).toBe("[0, 0, 0]");
    expect(rows[0].after).toBe("[0, 0, 250]");
  });

  it("shows a property that only one side has", () => {
    const [row] = diffFields({}, { folder: "Props" });
    expect(row).toMatchObject({ key: "folder", before: "—", after: "Props", changed: true });
  });

  it("gives up on non-objects so the raw view takes over", () => {
    expect(diffFields(undefined, undefined)).toEqual([]);
    expect(diffFields([1, 2], [1, 3])).toEqual([]);
  });
});

describe("formatValue", () => {
  it("keeps values whole rather than truncating them", () => {
    expect(formatValue(1234.5678)).toBe("1234.5678");
    expect(formatValue(null)).toBe("null");
    expect(formatValue(undefined)).toBe("—");
    expect(formatValue({ a: 1 })).toBe('{"a":1}');
  });
});

describe("opaqueDetail", () => {
  const before = {
    code: "import unreal\nspawn(5)",
    diff: {
      count: 3,
      added: 2,
      removed: 1,
      moved: 0,
      changes: [
        { change: "added", label: "Cube0", path: "/Game/x.Cube0" },
        { change: "added", label: "Cube1", path: "/Game/x.Cube1" },
        { change: "removed", label: "Old", path: "/Game/x.Old" },
      ],
      truncated: false,
    },
  };

  it("reads the code that ran and what the level snapshot noticed", () => {
    const detail = opaqueDetail(before)!;
    expect(detail.code).toContain("spawn(5)");
    expect([detail.added, detail.removed, detail.moved]).toEqual([2, 1, 0]);
    expect(detail.changes.map((c) => c.label)).toEqual(["Cube0", "Cube1", "Old"]);
  });

  it("a script that changed nothing still shows its code", () => {
    const detail = opaqueDetail({ code: "print(1)", diff: { count: 0, changes: [] } })!;
    expect(detail.code).toBe("print(1)");
    expect(detail.changes).toEqual([]);
  });

  it("an ordinary editor change is not mistaken for an opaque one", () => {
    // A transform's before-state has neither code nor a diff, so it keeps the table.
    expect(opaqueDetail({ location: [0, 0, 0] })).toBeNull();
    expect(opaqueDetail(null)).toBeNull();
    expect(opaqueDetail("not an object")).toBeNull();
  });

  it("falls back to the id when a change row has no path", () => {
    const detail = opaqueDetail({ diff: { changes: [{ change: "moved", id: "G7" }] } })!;
    expect(detail.changes[0]).toEqual({ change: "moved", label: "", path: "G7" });
  });
});

describe("prettyCode", () => {
  it("indents JSON so nested params are readable", () => {
    expect(prettyCode('{"a":1}')).toBe('{\n  "a": 1\n}');
  });
});

describe("isStructuredText", () => {
  it("treats objects as code, not short coordinate arrays", () => {
    expect(isStructuredText('{"folder":"/CardGame/Materials"}')).toBe(true);
    expect(isStructuredText("[0, 0, 250]")).toBe(false);
  });
});

describe("projectEditorDiff", () => {
  const envelope = (params: Record<string, unknown>, created: unknown[] = []) => ({
    params,
    after: null,
    created,
  });

  it("treats the journal after-blob as an envelope, not a property snapshot", () => {
    expect(isEditorEnvelope(envelope({ actor_path: "/x", label: "New" }))).toBe(true);
    expect(isEditorEnvelope({ location: [0, 0, 250] })).toBe(false);
  });

  it("a rename is the name, not params / created / after", () => {
    const rows = projectEditorDiff({ label: "Button" }, envelope({ actor_path: "/x", label: "LedgerWireTest_Button" }));
    expect(rows).toEqual([{ key: "Name", before: "Button", after: "LedgerWireTest_Button", changed: true }]);
    expect(editorStory(rows)).toBe("Renamed Button to LedgerWireTest_Button");
  });

  it("a wire is the field and who it points at", () => {
    const rows = projectEditorDiff(
      { field: "TestProps", target_paths: [], asset_paths: [], value: null },
      envelope({ actor_path: "/x", field: "TestProps", target_paths: ["WireProp_01", "WireProp_02"] }),
    );
    expect(rows).toEqual([
      { key: "TestProps", before: "—", after: "WireProp_01, WireProp_02", changed: true },
    ]);
    expect(editorStory(rows)).toBe("Set TestProps to WireProp_01, WireProp_02");
  });

  it("does not pretend a missing after-property was cleared", () => {
    expect(projectEditorDiff({ location: [0, 0, 0] }, envelope({ actor_path: "/x" }))).toEqual([]);
  });
});
