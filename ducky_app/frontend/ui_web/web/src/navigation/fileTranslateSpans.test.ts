import { describe, expect, it } from "vitest";

import {
  applyTranslateSpans,
  chunkPhrases,
  extractTranslateSpans,
  isTranslatablePhrase,
  translationMapIsNoop,
  uniqueSpanTexts,
} from "./fileTranslateSpans";

describe("fileTranslateSpans", () => {
  it("extracts // comments and string literals", () => {
    const src = [
      "using { /Verse.org/Simulation }",
      "",
      "// Helper for agents",
      'wrapper_agent(t : type) := class():',
      '    ExtraData : string = "Hello player"',
      "",
    ].join("\n");
    const spans = extractTranslateSpans(src);
    const texts = uniqueSpanTexts(spans);
    expect(texts).toContain(" Helper for agents");
    expect(texts).toContain("Hello player");
    expect(texts.some((t) => t.includes("wrapper_agent"))).toBe(false);
  });

  it("skips code-like and path-like phrases", () => {
    expect(isTranslatablePhrase("maxPlayers")).toBe(false);
    expect(isTranslatablePhrase("foo_bar")).toBe(false);
    expect(isTranslatablePhrase("/Verse.org/Simulation")).toBe(false);
    expect(isTranslatablePhrase("Hello player")).toBe(true);
  });

  it("applies translations without shifting later spans wrongly", () => {
    const src = '// First line\nMsg := "Hello player"\n';
    const spans = extractTranslateSpans(src);
    const map: Record<string, string> = {};
    for (const s of spans) {
      map[s.text] = s.text.includes("Hello") ? "Здравей играч" : " Първи ред";
    }
    const out = applyTranslateSpans(src, spans, map);
    expect(out).toContain("// Първи ред");
    expect(out).toContain('"Здравей играч"');
  });

  it("detects English echo maps", () => {
    expect(translationMapIsNoop(["Hi"], { Hi: "Hi" })).toBe(true);
    expect(translationMapIsNoop(["Hi"], { Hi: "Здравей" })).toBe(false);
  });

  it("chunks at 40", () => {
    const phrases = Array.from({ length: 45 }, (_, i) => `p${i}`);
    expect(chunkPhrases(phrases, 40)).toHaveLength(2);
    expect(chunkPhrases(phrases, 40)[0]).toHaveLength(40);
    expect(chunkPhrases(phrases, 40)[1]).toHaveLength(5);
  });
});
