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
export const LOOK_PX = 12;

/** nx/ny in -1..1 (right/down positive). Returns held WASD keys. */
export function stickMoveKeys(nx: number, ny: number, deadzone = STICK_DEADZONE): string[] {
  const mag = Math.hypot(nx, ny);
  if (mag < deadzone) return [];
  const keys: string[] = [];
  if (ny < -deadzone) keys.push("w");
  if (ny > deadzone) keys.push("s");
  if (nx < -deadzone) keys.push("a");
  if (nx > deadzone) keys.push("d");
  return keys;
}

/** Relative look pixels from a right-stick deflection (right/down positive). */
export function stickLookDelta(
  nx: number,
  ny: number,
  sensitivity = 1,
  deadzone = STICK_DEADZONE,
): { dx: number; dy: number } {
  const mag = Math.hypot(nx, ny);
  if (mag < deadzone) return { dx: 0, dy: 0 };
  const t = (mag - deadzone) / Math.max(0.01, 1 - deadzone);
  const scale = LOOK_PX * sensitivity * t;
  return { dx: Math.round((nx / mag) * scale), dy: Math.round((ny / mag) * scale) };
}

export function keyDiff(prev: string[], next: string[]): { down: string[]; up: string[] } {
  const have = new Set(prev);
  const want = new Set(next);
  return {
    down: next.filter((k) => !have.has(k)),
    up: prev.filter((k) => !want.has(k)),
  };
}
