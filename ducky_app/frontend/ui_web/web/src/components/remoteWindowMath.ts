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

/** Splash / Recent Projects hub — island windows append a project name. */
export function isUeFnHubTitle(title: string): boolean {
  return title.trim().toLowerCase() === "unreal editor for fortnite";
}

/** Next hwnd to watch after launch / splash death. `undefined` = keep current. */
export function pickUeFnFollow(args: {
  rows: { id: string; title: string; kind?: string }[];
  seenIds: Iterable<string>;
  pending: "hub" | "project" | null;
  selectedId: string;
}): string | undefined {
  const seen = new Set(args.seenIds);
  const uefn = args.rows.filter((r) => r.kind === "uefn");
  const island = uefn.find((r) => !isUeFnHubTitle(r.title));
  if (args.pending === "project") return island?.id;
  if (args.pending === "hub") return uefn.find((r) => !seen.has(r.id))?.id ?? uefn[0]?.id;
  if (args.selectedId && !args.rows.some((r) => r.id === args.selectedId)) {
    return island?.id ?? uefn[0]?.id ?? "";
  }
  if (args.selectedId) {
    const cur = args.rows.find((r) => r.id === args.selectedId);
    if (cur && isUeFnHubTitle(cur.title) && island) return island.id;
  }
  return undefined;
}

/** Local Chrome-style pan/zoom of the stream view (not sent to the desktop). */
export const VIEW_SCALE_MIN = 1;
export const VIEW_SCALE_MAX = 4;

export type ViewPanZoom = { panX: number; panY: number; scale: number };

export function clampViewScale(scale: number): number {
  return Math.min(VIEW_SCALE_MAX, Math.max(VIEW_SCALE_MIN, scale));
}

export function viewOrigin(view: ContentBox): { x: number; y: number } {
  return { x: view.left + view.width / 2, y: view.top + view.height / 2 };
}

/** Client → untransformed view coords (transform-origin at view center). */
export function mapViewPoint(
  clientX: number,
  clientY: number,
  view: ContentBox,
  panX: number,
  panY: number,
  scale: number,
): { x: number; y: number } {
  const s = scale || 1;
  const o = viewOrigin(view);
  return {
    x: o.x + (clientX - o.x - panX) / s,
    y: o.y + (clientY - o.y - panY) / s,
  };
}

export function clampView(view: ContentBox, panX: number, panY: number, scale: number): ViewPanZoom {
  const s = clampViewScale(scale);
  const maxX = ((s - 1) * view.width) / 2;
  const maxY = ((s - 1) * view.height) / 2;
  return {
    panX: Math.min(maxX, Math.max(-maxX, panX)) + 0,
    panY: Math.min(maxY, Math.max(-maxY, panY)) + 0,
    scale: s,
  };
}

export function panZoomAround(
  view: ContentBox,
  panX: number,
  panY: number,
  scale: number,
  nextScale: number,
  aroundX: number,
  aroundY: number,
): ViewPanZoom {
  const s0 = scale || 1;
  const s1 = clampViewScale(nextScale);
  const o = viewOrigin(view);
  const ratio = s1 / s0;
  return clampView(
    view,
    aroundX - o.x - (aroundX - o.x - panX) * ratio,
    aroundY - o.y - (aroundY - o.y - panY) * ratio,
    s1,
  );
}

export function addViewPan(
  view: ContentBox,
  panX: number,
  panY: number,
  scale: number,
  dx: number,
  dy: number,
): ViewPanZoom {
  return clampView(view, panX + dx, panY + dy, scale);
}

export function pinchScale(prevDist: number, nextDist: number, scale: number): number {
  if (prevDist < 1) return clampViewScale(scale);
  return clampViewScale(scale * (nextDist / prevDist));
}

export function twoPointDist(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

export function twoPointCenter(a: { x: number; y: number }, b: { x: number; y: number }): { x: number; y: number } {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

/** Finger jitter under this CSS-px distance is still a tap, not a drag. */
export const TOUCH_SLOP = 12;
/** Second tap within this window at the first tap's pixel is a double-click. */
export const DBLTAP_MS = 350;
export const DBLTAP_PX = 24;

export function withinSlop(ax: number, ay: number, bx: number, by: number, slop = TOUCH_SLOP): boolean {
  return Math.hypot(bx - ax, by - ay) <= slop;
}

export function isDoubleTap(
  prev: { x: number; y: number; at: number } | null,
  next: { x: number; y: number; at: number },
  ms = DBLTAP_MS,
  px = DBLTAP_PX,
): boolean {
  if (!prev) return false;
  return next.at - prev.at <= ms && Math.hypot(next.x - prev.x, next.y - prev.y) <= px;
}

/** Ghost mouse after an overlay touch — ignore mouse pointers on the video. */
export const MOUSE_AFTER_TOUCH_MS = 500;

export function ignoreMouseAfterTouch(lastTouchAt: number, now: number, ms = MOUSE_AFTER_TOUCH_MS): boolean {
  if (lastTouchAt <= 0) return false;
  return now - lastTouchAt < ms;
}

/** Control+z → keydown Control, z, keyup z, Control. */
export function chordTap(keys: string[]): { type: "keydown" | "keyup"; key: string }[] {
  return [
    ...keys.map((key) => ({ type: "keydown" as const, key })),
    ...[...keys].reverse().map((key) => ({ type: "keyup" as const, key })),
  ];
}

/** RMB look / LMB release without teleporting the cursor. */
export function buttonOnly(kind: "down" | "up", button: number): { type: "down" | "up"; button: number } {
  return { type: kind, button };
}
