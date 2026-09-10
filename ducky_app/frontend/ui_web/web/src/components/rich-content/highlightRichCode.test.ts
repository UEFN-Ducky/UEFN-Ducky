import { describe, expect, it } from "vitest";
import { highlightRichCode } from "./highlightRichCode";

describe("highlightRichCode", () => {
  it("paints comments, strings and calls in the UMG fence", () => {
    const src = `umg_capabilities()   # probe first
create_widget_blueprint(asset_name="UW_LedgerTestHud", folder="/<Project>/UI")`;
    const kinds = highlightRichCode(src).filter((s) => s.kind).map((s) => [s.kind, s.text]);
    expect(kinds).toContainEqual(["fn", "umg_capabilities"]);
    expect(kinds).toContainEqual(["comment", "# probe first"]);
    expect(kinds).toContainEqual(["fn", "create_widget_blueprint"]);
    expect(kinds).toContainEqual(["string", '"UW_LedgerTestHud"']);
  });
});
