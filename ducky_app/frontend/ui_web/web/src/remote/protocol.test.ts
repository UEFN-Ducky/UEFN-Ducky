import { describe, expect, it } from "vitest";
import {
  MAX_CHUNK_BYTES,
  PartAssembler,
  blobPathAllowed,
  compareVersions,
  rewriteLoopbackUrls,
  sdpFingerprint,
  sendPaced,
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
    expect(blobPathAllowed("/workflow-media/0123abcd/dG9r/duck.png")).toBe(true);
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

  it("rewrites loopback URLs inside chat markdown and JSON-as-text tool results", () => {
    const base = "/static/plugins/uefn-ducky/panel/";
    const origins = ["http://127.0.0.1:4199", "http://localhost:4199"];
    const md = "Map:\n![Top](http://127.0.0.1:4199/chat-attachments/c1/s1.png)\n![B](http://127.0.0.1:4199/chat-attachments/c1/s2.png)";
    expect(rewriteLoopbackUrls(md, origins, base)).toBe(
      `Map:\n![Top](${base}chat-attachments/c1/s1.png)\n![B](${base}chat-attachments/c1/s2.png)`,
    );
    const tool = JSON.stringify({ path: "C:/x/s1.png", media_url: "http://127.0.0.1:4199/chat-attachments/c1/s1.png" });
    expect(JSON.parse(rewriteLoopbackUrls(tool, origins, base)).media_url).toBe(`${base}chat-attachments/c1/s1.png`);
    expect(rewriteLoopbackUrls("http://127.0.0.1:41990/x and https://example.com/y", origins, base)).toBe(
      "http://127.0.0.1:41990/x and https://example.com/y",
    );
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

describe("sendPaced", () => {
  // Oct 30 2026: a long chat's reply sent in one go hit Chromium's 16 MB "send queue is full".
  class FakeChannel {
    readyState = "open";
    bufferedAmount = 0;
    bufferedAmountLowThreshold = 0;
    sent: string[] = [];
    private listeners = new Map<string, Set<() => void>>();
    send(data: string) {
      if (this.bufferedAmount > 1024 * 1024) throw new Error("send queue is full");
      this.sent.push(data);
      this.bufferedAmount += data.length;
    }
    addEventListener(type: string, fn: () => void) {
      if (!this.listeners.has(type)) this.listeners.set(type, new Set());
      this.listeners.get(type)!.add(fn);
    }
    removeEventListener(type: string, fn: () => void) {
      this.listeners.get(type)?.delete(fn);
    }
    drain() {
      this.bufferedAmount = 0;
      for (const fn of [...(this.listeners.get("bufferedamountlow") ?? [])]) fn();
    }
  }

  it("waits for the queue to drain instead of overfilling it, and keeps order", async () => {
    const ch = new FakeChannel();
    const big = Array.from({ length: 40 }, (_, i) => `${i}:`.padEnd(100 * 1024, "x"));
    const first = sendPaced(ch, big);
    const second = sendPaced(ch, ["after"]);
    await Promise.resolve();
    expect(ch.sent.length).toBeLessThan(big.length);
    for (let i = 0; i < 40 && ch.sent.length < 41; i++) {
      ch.drain();
      await new Promise((r) => setTimeout(r, 0));
    }
    await first;
    await second;
    expect(ch.sent.map((s) => s.split(":")[0])).toEqual([...big.map((_, i) => String(i)), "after"]);
  });

  it("stops when the channel closes while it waits", async () => {
    const ch = new FakeChannel();
    ch.bufferedAmount = 10 * 1024 * 1024;
    const done = sendPaced(ch, ["a", "b"]);
    ch.readyState = "closed";
    for (const fn of [...((ch as unknown as { listeners: Map<string, Set<() => void>> }).listeners.get("close") ?? [])]) fn();
    await done;
    expect(ch.sent).toEqual([]);
  });
});
