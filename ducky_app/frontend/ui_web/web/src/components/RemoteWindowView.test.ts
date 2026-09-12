import { describe, expect, it } from "vitest";
import { contentRect, keyDiff, rankVideoCodec, stickLookDelta, stickMoveKeys } from "./remoteWindowMath";

describe("rankVideoCodec", () => {
  it("prefers hardware-friendly H.264 profiles, then AV1, VP9, VP8", () => {
    const high = rankVideoCodec({ mimeType: "video/H264", sdpFmtpLine: "profile-level-id=640c1f" });
    const main = rankVideoCodec({ mimeType: "video/H264", sdpFmtpLine: "profile-level-id=4d001f" });
    const base = rankVideoCodec({ mimeType: "video/H264", sdpFmtpLine: "profile-level-id=42e01f" });
    const av1 = rankVideoCodec({ mimeType: "video/AV1" });
    const vp9 = rankVideoCodec({ mimeType: "video/VP9" });
    const vp8 = rankVideoCodec({ mimeType: "video/VP8" });
    const rtx = rankVideoCodec({ mimeType: "video/rtx" });
    expect([high, main, base, av1, vp9, vp8, rtx]).toEqual([...[high, main, base, av1, vp9, vp8, rtx]].sort((a, b) => a - b));
    expect(high).toBeLessThan(main);
    expect(main).toBeLessThan(base);
    expect(base).toBeLessThan(av1);
    expect(vp8).toBeLessThan(rtx);
  });
});

describe("contentRect", () => {
  it("letterboxes a wide video inside a tall box", () => {
    const r = contentRect({ left: 0, top: 0, width: 400, height: 800 }, 1600, 900);
    expect(r.width).toBe(400);
    expect(r.height).toBe(225);
    expect(r.left).toBe(0);
    expect(r.top).toBe(287.5);
  });

  it("pillarboxes a tall video inside a wide box", () => {
    const r = contentRect({ left: 10, top: 20, width: 800, height: 400 }, 480, 1040);
    expect(r.height).toBe(400);
    expect(r.width).toBeCloseTo(184.6, 1);
    expect(r.left).toBeCloseTo(10 + (800 - 184.6) / 2, 1);
    expect(r.top).toBe(20);
  });

  it("returns the box itself before the video has dimensions", () => {
    const box = { left: 1, top: 2, width: 300, height: 200 };
    expect(contentRect(box, 0, 0)).toBe(box);
  });
});

describe("stickMoveKeys", () => {
  it("ignores the deadzone and maps WASD", () => {
    expect(stickMoveKeys(0, 0)).toEqual([]);
    expect(stickMoveKeys(0, -1)).toEqual(["w"]);
    expect(stickMoveKeys(1, 0)).toEqual(["d"]);
    expect(stickMoveKeys(-0.9, 0.9).sort()).toEqual(["a", "s"]);
  });
});

describe("stickLookDelta / keyDiff", () => {
  it("deadzone is zero and full-right is positive dx", () => {
    expect(stickLookDelta(0, 0)).toEqual({ dx: 0, dy: 0 });
    expect(stickLookDelta(0.1, 0)).toEqual({ dx: 0, dy: 0 });
    const look = stickLookDelta(1, 0);
    expect(look.dx).toBeGreaterThan(0);
    expect(look.dy).toBe(0);
    expect(keyDiff(["w"], ["w", "d"])).toEqual({ down: ["d"], up: [] });
    expect(keyDiff(["w", "a"], ["d"])).toEqual({ down: ["d"], up: ["w", "a"] });
  });
});
