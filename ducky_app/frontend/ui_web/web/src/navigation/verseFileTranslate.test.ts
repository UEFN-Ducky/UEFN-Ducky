import { describe, expect, it } from "vitest";

import {
  chunkNeedsLlm,
  looksLikeWeakTranslation,
  splitIntoLineChunks,
  stripTranslateFences,
  verseChunkCacheKey,
  verseTranslateCacheKey,
} from "./verseFileTranslate";

describe("verseFileTranslate", () => {
  it("chunks on line boundaries under max chars", () => {
    const lines = Array.from({ length: 40 }, (_, i) => `line_${i}_xxxxxxxx\n`);
    const text = lines.join("");
    const chunks = splitIntoLineChunks(text, 120);
    expect(chunks.length).toBeGreaterThan(1);
    expect(chunks.join("")).toBe(text);
    for (const c of chunks) expect(c.length).toBeLessThanOrEqual(140);
  });

  it("skips LLM for blank / path-only chunks", () => {
    expect(chunkNeedsLlm("\n\n")).toBe(false);
    expect(chunkNeedsLlm("using { /Verse.org/Simulation }\n")).toBe(true);
    expect(chunkNeedsLlm("  /UnrealEngine.com/Temporary/UI  \n")).toBe(false);
  });

  it("rejects lazy keyword-only swaps as weak", () => {
    const source = [
      "using { /Verse.org/Simulation }",
      "SubscribeAgent<t>(Listenable: listenable(t), OutputFunc: type{_(:agent):void}, ExtraData: t): cancelable =",
      "    wrapper_agent(t){Listenable := Listenable, OutputFunc := OutputFunc, ExtraData := ExtraData}.Subscribe()",
      "wrapper_agent(t : type) := class():",
      "    var Listenable : listenable(t)",
      "    OutputFunc : type{_(:agent):void}",
      "    ExtraData : t",
      "    Subscribe()<suspends>:cancelable=",
      "        Listenable.Subscribe(OnListened)",
      "    OnListened(Agent:agent):void=",
      "        OutputFunc(Agent)",
    ].join("\n");
    const lazy = source.replaceAll("using", "използване");
    expect(looksLikeWeakTranslation(source, lazy)).toBe(true);
    expect(looksLikeEnglishEchoCompat(source, lazy)).toBe(false);
  });

  it("accepts a heavily localized rewrite", () => {
    const source = [
      "using { /Verse.org/Simulation }",
      "SubscribeAgent<t>(Listenable: listenable(t), OutputFunc: type{_(:agent):void}): cancelable =",
      "    wrapper_agent(t){Listenable := Listenable}.Subscribe()",
      "wrapper_agent(t : type) := class():",
      "    var Listenable : listenable(t)",
      "    Subscribe()<suspends>:cancelable=",
      "        Listenable.Subscribe(OnListened)",
      "    OnListened(Agent:agent):void=",
      "        OutputFunc(Agent)",
    ].join("\n");
    const good = [
      "използване { /Verse.org/Simulation }",
      "АбонирайАгент<т>(Слушаемо: слушаемо(т), ИзходнаФункция: тип{_(:агент):празно}): отменяемо =",
      "    обвивка_агент(т){Слушаемо := Слушаемо}.Абонирай()",
      "обвивка_агент(т : тип) := клас():",
      "    пром Слушаемо : слушаемо(т)",
      "    Абонирай()<спиране>:отменяемо=",
      "        Слушаемо.Абонирай(ПриСлушане)",
      "    ПриСлушане(Агент:агент):празно=",
      "        ИзходнаФункция(Агент)",
    ].join("\n");
    expect(looksLikeWeakTranslation(source, good)).toBe(false);
  });

  it("strips fences and builds vf_/vc_ cache keys", () => {
    expect(stripTranslateFences("```verse\nfoo\n```")).toBe("foo");
    expect(verseTranslateCacheKey("Bulgarian", "abc123def")).toBe("vf_bulgarian_abc123def");
    expect(verseChunkCacheKey("Bulgarian", "abc123def")).toBe("vc_bulgarian_abc123def");
  });
});

/** Old char-alignment check — proves why lazy swaps used to pass. */
function looksLikeEnglishEchoCompat(source: string, translated: string): boolean {
  const a = source.replace(/\s+/g, " ").trim();
  const b = translated.replace(/\s+/g, " ").trim();
  if (!b || a === b) return true;
  const n = Math.min(a.length, b.length);
  if (n < 40) return a === b;
  let same = 0;
  for (let i = 0; i < n; i++) if (a[i] === b[i]) same++;
  return same / n > 0.92;
}
