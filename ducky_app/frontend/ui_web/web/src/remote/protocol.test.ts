import { describe, expect, it } from "vitest";
import {
  MAX_CHUNK_BYTES,
  PartAssembler,
  blobPathAllowed,
  compareVersions,
  rewriteLoopbackUrls,
  sdpFingerprint,
  splitText,
} from "./protocol";

describe("splitText / PartAssembler", () => {
  it("keeps small messages whole", () => {
    expect(splitText("hello")).toEqual(["hello"]);
  });

  it("splits at the chunk cap and reassembles in any order", () => {
    const text = "x".repeat(MAX_CHUNK_BYTES * 2 + 17);
    const parts = splitText(text);
    expect(parts.length).toBe(3);
    expect(parts.every((p) => p.length <= MAX_CHUNK_BYTES)).toBe(true);
    const asm = new PartAssembler();
    expect(asm.push(7, 2, 3, parts[2])).toBeNull();
    expect(asm.push(7, 0, 3, parts[0])).toBeNull();
    expect(asm.push(7, 1, 3, parts[1])).toBe(text);
  });

  it("never splits a surrogate pair", () => {
    const emoji = "😀";
    const text = "a".repeat(MAX_CHUNK_BYTES - 1) + emoji + "b";
    const parts = splitText(text);
    expect(parts.join("")).toBe(text);
    for (const p of parts) expect(() => encodeURIComponent(p)).not.toThrow();
  });
});

describe("blobPathAllowed", () => {
  it("allows only the desktop asset prefixes", () => {
    expect(blobPathAllowed("/plugin-ui/foo/index.html")).toBe(true);
    expect(blobPathAllowed("/user-sounds/ding.mp3")).toBe(true);
    expect(blobPathAllowed("/model-files/abc/x.glb")).toBe(true);
    expect(blobPathAllowed("/__panel_api/list_chats")).toBe(false);
    expect(blobPathAllowed("/plugin-ui/../__panel_api/x")).toBe(false);
    expect(blobPathAllowed("/etc/passwd")).toBe(false);
  });
});

describe("rewriteLoopbackUrls", () => {
  it("rewrites nested loopback URLs to same-origin paths", () => {
    const input = {
      url: "http://127.0.0.1:4199/model-files/abc/x.png",
      list: ["http://localhost:4199/tool-captures/1.png", "https://example.com/keep"],
      n: 3,
    };
    const out = rewriteLoopbackUrls(input, ["http://127.0.0.1:4199", "http://localhost:4199"]);
    expect(out).toEqual({
      url: "/model-files/abc/x.png",
      list: ["/tool-captures/1.png", "https://example.com/keep"],
      n: 3,
    });
  });
});

describe("sdpFingerprint / compareVersions", () => {
  it("extracts the DTLS fingerprint", () => {
    expect(sdpFingerprint("v=0\r\na=fingerprint:sha-256 ab:CD:12\r\n")).toBe("sha-256 AB:CD:12");
    expect(sdpFingerprint("v=0")).toBe("");
  });

  it("compares dotted versions numerically", () => {
    expect(compareVersions("1.2.10", "1.2.9")).toBe(1);
    expect(compareVersions("1.2.9", "1.2.10")).toBe(-1);
    expect(compareVersions("1.2.42", "1.2.42")).toBe(0);
  });
});
