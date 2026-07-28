import { describe, expect, it } from "vitest";
import { formatVerseDocument } from "./verseFormatter";

describe("formatVerseDocument", () => {
  it("normalizes a 2-space class body to 4-space levels", () => {
    const input = ["my_device := class(creative_device):", "  Field:int = 0", "  Go():void ="].join("\n");
    const expected = [
      "my_device := class(creative_device):",
      "    Field:int = 0",
      "    Go():void =",
      "",
    ].join("\n");
    expect(formatVerseDocument(input)).toBe(expected);
  });

  it("converts leading tabs to spaces and keeps nesting", () => {
    const input = ["Go():void =", "\tPrint(\"a\")", "\tif (X?):", "\t\tPrint(\"b\")"].join("\n");
    const expected = [
      "Go():void =",
      "    Print(\"a\")",
      "    if (X?):",
      "        Print(\"b\")",
      "",
    ].join("\n");
    expect(formatVerseDocument(input)).toBe(expected);
  });

  it("does NOT cascade colon/= suites rightward (regression guard)", () => {
    // Three sibling headers at the same author level must stay at the same level.
    const input = ["A():void =", "    if (P?):", "        Do()", "    if (Q?):", "        Do()"].join("\n");
    expect(formatVerseDocument(input)).toBe(input + "\n");
  });

  it("trims trailing whitespace and collapses blank runs", () => {
    const input = ["Foo()   ", "", "", "", "Bar()"].join("\n");
    const expected = ["Foo()", "", "Bar()", ""].join("\n");
    expect(formatVerseDocument(input)).toBe(expected);
  });

  it("guarantees exactly one trailing newline", () => {
    expect(formatVerseDocument("X()")).toBe("X()\n");
    expect(formatVerseDocument("X()\n\n\n")).toBe("X()\n");
  });

  it("leaves block-comment interiors untouched", () => {
    const input = ["<#", "   art   ", "     kept as-is", "#>", "Code()"].join("\n");
    const expected = ["<#", "   art   ", "     kept as-is", "#>", "Code()", ""].join("\n");
    expect(formatVerseDocument(input)).toBe(expected);
  });

  it("preserves CRLF line endings", () => {
    const input = "A():void =\r\n\tGo()";
    expect(formatVerseDocument(input)).toBe("A():void =\r\n    Go()\r\n");
  });

  it("returns whitespace-only input unchanged", () => {
    expect(formatVerseDocument("   \n\n")).toBe("   \n\n");
  });
});
