import { describe, expect, it } from "vitest";
import { editorPrimaryLabel } from "./duckyProfileModalLabels";

describe("editorPrimaryLabel", () => {
  it("stays Create ducky when only the picker is creating", () => {
    expect(editorPrimaryLabel({ saving: false, isCreate: true, hasUnsavedChanges: false })).toBe(
      "Create ducky",
    );
  });

  it("says Saving… only for an editor save", () => {
    expect(editorPrimaryLabel({ saving: true, isCreate: true, hasUnsavedChanges: false })).toBe(
      "Saving…",
    );
  });
});
