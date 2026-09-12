/** Pure Remote View helpers — kept off the React/Worker import graph so tests can run in Node. */

export type ContentBox = { left: number; top: number; width: number; height: number };

/** H.264 High > Main > Baseline (hardware paths on Windows), then AV1, VP9, VP8. */
export function rankVideoCodec(codec: { mimeType: string; sdpFmtpLine?: string }): number {
  const mime = codec.mimeType.toLowerCase();
  const fmtp = (codec.sdpFmtpLine || "").toLowerCase();
  if (mime.endsWith("/h264")) {
    const m = /profile-level-id=([0-9a-f]{2})/.exec(fmtp);
    const profile = m?.[1] ?? "";
    if (profile === "64") return 0;
    if (profile === "4d") return 1;
    return 2;
  }
  if (mime.endsWith("/av1")) return 3;
  if (mime.endsWith("/vp9")) return 4;
  if (mime.endsWith("/vp8")) return 5;
  return 8;
}

/** Where the video pixels actually sit inside an object-fit: contain element. */
export function contentRect(
  box: ContentBox,
  videoWidth: number,
  videoHeight: number,
): ContentBox {
  if (!videoWidth || !videoHeight || !box.width || !box.height) return box;
  const s = Math.min(box.width / videoWidth, box.height / videoHeight);
  const w = videoWidth * s;
  const h = videoHeight * s;
  return {
    left: box.left + (box.width - w) / 2,
    top: box.top + (box.height - h) / 2,
    width: w,
    height: h,
  };
}

export const STICK_DEADZONE = 0.28;

/** nx/ny in -1..1 (right/down positive). Returns held WASD keys. */
export function stickMoveKeys(nx: number, ny: number): string[] {
  const mag = Math.hypot(nx, ny);
  if (mag < STICK_DEADZONE) return [];
  const keys: string[] = [];
  if (ny < -STICK_DEADZONE) keys.push("w");
  if (ny > STICK_DEADZONE) keys.push("s");
  if (nx < -STICK_DEADZONE) keys.push("a");
  if (nx > STICK_DEADZONE) keys.push("d");
  return keys;
}

/** Look offset in normalized video space from a right-stick deflection. */
export function stickLookPoint(nx: number, ny: number, scale = 0.16): { x: number; y: number } {
  const mag = Math.hypot(nx, ny);
  if (mag < STICK_DEADZONE) return { x: 0.5, y: 0.5 };
  const t = (mag - STICK_DEADZONE) / (1 - STICK_DEADZONE);
  return {
    x: Math.min(1, Math.max(0, 0.5 + nx * t * scale)),
    y: Math.min(1, Math.max(0, 0.5 + ny * t * scale)),
  };
}

export function keyDiff(prev: string[], next: string[]): { down: string[]; up: string[] } {
  const have = new Set(prev);
  const want = new Set(next);
  return {
    down: next.filter((k) => !have.has(k)),
    up: prev.filter((k) => !want.has(k)),
  };
}
