import { describe, expect, it } from "vitest";
import { DEFAULT_CSS_VARS } from "./defaultTokens";
import { FONT_META_KEYS, parseFontLibrary, UI_FONT_SIZE_DEFAULT } from "./fontLibrary";

describe("default UI font size", () => {
  it("is 13 px for someone who never picked one", () => {
    expect(UI_FONT_SIZE_DEFAULT).toBe(13);
    expect(parseFontLibrary({}).uiSize).toBe(13);
    expect(DEFAULT_CSS_VARS["font-ui-size"]).toBe("13px");
  });

  it("keeps a saved size", () => {
    expect(parseFontLibrary({ [FONT_META_KEYS.uiSize]: "12" }).uiSize).toBe(12);
  });
});
