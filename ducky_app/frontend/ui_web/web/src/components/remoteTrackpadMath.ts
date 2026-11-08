/** Phone trackpad and air mouse: gestures and sensor maths, no DOM. */

/** View-menu id for the trackpad; never a window id. */
export const REMOTE_TRACKPAD_ID = "trackpad";

/** The view the page URL asks to open on; only the trackpad can be asked for. */
export function initialRemoteView(search: string): string {
  return new URLSearchParams(search).get("view") === REMOTE_TRACKPAD_ID ? REMOTE_TRACKPAD_ID : "";
}

export const HOLD_MS = 350;
export const SLOP_PX = 8;
export const SCROLL_STEP_PX = 24;
export const MOTION_DEADZONE_DEG = 2;
export const MOTION_PX_PER_DEG = 24;

export type PadTouch = { startX: number; startY: number; lastX: number; lastY: number; moved: boolean; dragging: boolean };

export function padDown(x: number, y: number): PadTouch {
  return { startX: x, startY: y, lastX: x, lastY: y, moved: false, dragging: false };
}

/** Cursor pixels for this finger move; nothing until it leaves the tap slop. */
export function padMove(touch: PadTouch, x: number, y: number, gain: number): { dx: number; dy: number } {
  if (!touch.moved && Math.hypot(x - touch.startX, y - touch.startY) < SLOP_PX) return { dx: 0, dy: 0 };
  touch.moved = true;
  const out = { dx: (x - touch.lastX) * gain, dy: (y - touch.lastY) * gain };
  touch.lastX = x;
  touch.lastY = y;
  return out;
}

/** The hold timer fired: does this touch become a drag? */
export function padHoldStartsDrag(touch: PadTouch): boolean {
  return !touch.moved && !touch.dragging;
}

export function padUp(touch: PadTouch): "click" | "drop" | "none" {
  if (touch.dragging) return "drop";
  return touch.moved ? "none" : "click";
}

/** Whole wheel notches in `acc + dy`. Finger down = content follows it = wheel up (positive). */
export function scrollSteps(acc: number, dy: number): { steps: number; acc: number } {
  const total = acc + dy;
  const steps = Math.trunc(total / SCROLL_STEP_PX);
  return { steps, acc: total - steps * SCROLL_STEP_PX };
}

/** Whole pixels to send now; the fraction stays for the next frame so slow moves still add up. */
export function takeWhole(x: number, y: number): { dx: number; dy: number; restX: number; restY: number } {
  const dx = Math.trunc(x);
  const dy = Math.trunc(y);
  return { dx, dy, restX: x - dx, restY: y - dy };
}

function pastDeadzone(rate: number): number {
  return Math.abs(rate) <= MOTION_DEADZONE_DEG ? 0 : rate - Math.sign(rate) * MOTION_DEADZONE_DEG;
}

export type MotionTune = { sensitivity: number; invertX: boolean; invertY: boolean };

/**
 * Gyro (deg/s) to cursor pixels. Left/right is the turn around real-world up, taken from gravity,
 * so it works flat in the hand, upright, or anywhere between.
 *
 * ponytail: browsers disagree on gravity's sign, so "up" is whichever way has the screen or top
 * edge rising; a phone held upside down reads mirrored. Invert X/Y are the calibration knobs.
 */
export function motionToCursor(
  rate: { alpha: number; beta: number; gamma: number },
  gravity: { x: number; y: number; z: number } | null,
  dtSeconds: number,
  tune: MotionTune,
): { dx: number; dy: number } {
  const size = gravity ? Math.hypot(gravity.x, gravity.y, gravity.z) : 0;
  let up = size > 0.001 && gravity ? { x: gravity.x / size, y: gravity.y / size, z: gravity.z / size } : { x: 0, y: 0, z: 1 };
  if (up.y + up.z < 0) up = { x: -up.x, y: -up.y, z: -up.z };
  const yaw = pastDeadzone(rate.beta * up.x + rate.gamma * up.y + rate.alpha * up.z);
  const pitch = pastDeadzone(rate.beta);
  const px = dtSeconds * MOTION_PX_PER_DEG * tune.sensitivity;
  return { dx: -yaw * px * (tune.invertX ? -1 : 1), dy: -pitch * px * (tune.invertY ? -1 : 1) };
}
