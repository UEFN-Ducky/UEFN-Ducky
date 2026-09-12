import { describe, expect, it } from "vitest";

import { encodeWavPcm16, isTooShortRecording, pickTranscriptionBackend } from "./transcriptionSession";
import { normalizeSttProvider } from "./voiceSettings";

describe("encodeWavPcm16", () => {
  it("writes a valid mono 16-bit WAV header", async () => {
    const samples = new Float32Array([0, 0.5, -0.5, 0]);
    const blob = encodeWavPcm16(samples, 16000);
    expect(blob.type).toBe("audio/wav");
    expect(blob.size).toBe(44 + samples.length * 2);
    const bytes = new Uint8Array(await blob.arrayBuffer());
    expect(String.fromCharCode(...bytes.subarray(0, 4))).toBe("RIFF");
    expect(String.fromCharCode(...bytes.subarray(8, 12))).toBe("WAVE");
    expect(String.fromCharCode(...bytes.subarray(12, 16))).toBe("fmt ");
    expect(String.fromCharCode(...bytes.subarray(36, 40))).toBe("data");
  });
});

describe("isTooShortRecording", () => {
  it("rejects empty and silent buffers", () => {
    expect(isTooShortRecording(new Float32Array(0), 24000)).toBe(true);
    expect(isTooShortRecording(new Float32Array(24000), 24000)).toBe(true);
  });

  it("accepts a second of audible speech", () => {
    const samples = new Float32Array(24000);
    for (let i = 0; i < samples.length; i += 1) samples[i] = Math.sin(i / 20) * 0.2;
    expect(isTooShortRecording(samples, 24000)).toBe(false);
  });
});

describe("pickTranscriptionBackend", () => {
  it("defaults to browser speech when it exists", () => {
    expect(
      pickTranscriptionBackend({ preference: "", speechAvailable: true, openaiReady: false }),
    ).toBe("webspeech");
    expect(
      pickTranscriptionBackend({ preference: "", speechAvailable: true, openaiReady: true }),
    ).toBe("webspeech");
  });

  it("uses OpenAI only when asked or when browser speech is missing", () => {
    expect(
      pickTranscriptionBackend({ preference: "openai", speechAvailable: true, openaiReady: true }),
    ).toBe("openai");
    expect(
      pickTranscriptionBackend({ preference: "", speechAvailable: false, openaiReady: true }),
    ).toBe("openai");
  });

  it("falls back to browser speech when OpenAI is picked but has no key", () => {
    expect(
      pickTranscriptionBackend({ preference: "openai", speechAvailable: true, openaiReady: false }),
    ).toBe("webspeech");
  });

  it("normalizes listen preference", () => {
    expect(normalizeSttProvider("OpenAI")).toBe("openai");
    expect(normalizeSttProvider("default")).toBe("");
    expect(normalizeSttProvider("")).toBe("");
  });
});
