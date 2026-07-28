import { describe, expect, it } from "vitest";

import { detectSpeechLang, pickVoiceForText, voiceLangTag } from "./voiceMatch";

describe("detectSpeechLang", () => {
  it("detects Chinese", () => {
    expect(detectSpeechLang("打开的计划标签页将显示计划的翻译视图")).toBe("zh");
  });

  it("detects Japanese via kana", () => {
    expect(detectSpeechLang("これはテストです")).toBe("ja");
  });

  it("detects Korean", () => {
    expect(detectSpeechLang("안녕하세요")).toBe("ko");
  });

  it("defaults Latin to en", () => {
    expect(detectSpeechLang("Hello plan translation view")).toBe("en");
  });
});

describe("voiceLangTag", () => {
  it("parses piper voice ids", () => {
    expect(voiceLangTag("plugin:piper:en_US-lessac-medium")).toBe("en");
    expect(voiceLangTag("plugin:piper:zh_CN-huayan-medium")).toBe("zh");
  });

  it("parses builtin BCP-47-ish names", () => {
    expect(voiceLangTag("zh-CN")).toBe("zh");
    expect(voiceLangTag("Microsoft Huihui - Chinese (Simplified, PRC)")).toBe("zh");
  });
});

describe("pickVoiceForText", () => {
  it("keeps preferred for English text", () => {
    expect(pickVoiceForText("Hello world", "plugin:piper:en_US-lessac-medium")).toBe(
      "plugin:piper:en_US-lessac-medium",
    );
  });

  it("swaps to a Chinese builtin for CJK when preferred is English Piper", () => {
    const voices = [
      { name: "Microsoft David", lang: "en-US", voiceURI: "en-david" },
      { name: "Microsoft Huihui", lang: "zh-CN", voiceURI: "zh-huihui" },
    ];
    const prev = globalThis.speechSynthesis;
    Object.defineProperty(globalThis, "speechSynthesis", {
      configurable: true,
      value: { getVoices: () => voices },
    });
    try {
      expect(pickVoiceForText("打开的计划标签页", "plugin:piper:en_US-lessac-medium")).toBe(
        "builtin:Microsoft Huihui",
      );
    } finally {
      Object.defineProperty(globalThis, "speechSynthesis", {
        configurable: true,
        value: prev,
      });
    }
  });
});
