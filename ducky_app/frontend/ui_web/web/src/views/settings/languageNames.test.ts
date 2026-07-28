import { describe, expect, it } from "vitest";
import { checkLanguageName } from "./languageNames";

describe("checkLanguageName", () => {
  it("accepts exact and code aliases", () => {
    expect(checkLanguageName("Bulgarian")).toEqual({ kind: "ok", name: "Bulgarian" });
    expect(checkLanguageName("bg")).toEqual({ kind: "ok", name: "Bulgarian" });
    expect(checkLanguageName("ja")).toEqual({ kind: "ok", name: "Japanese" });
    expect(checkLanguageName("Français")).toEqual({ kind: "ok", name: "French" });
  });

  it("suggests close typos", () => {
    expect(checkLanguageName("Buldwgarian")).toEqual({
      kind: "suggest",
      input: "Buldwgarian",
      suggestion: "Bulgarian",
    });
    expect(checkLanguageName("Spanis")).toEqual({
      kind: "suggest",
      input: "Spanis",
      suggestion: "Spanish",
    });
  });

  it("rejects unknown gibberish", () => {
    expect(checkLanguageName("asdfgh")).toEqual({ kind: "unknown", input: "asdfgh" });
    expect(checkLanguageName("")).toEqual({ kind: "unknown", input: "" });
  });
});
