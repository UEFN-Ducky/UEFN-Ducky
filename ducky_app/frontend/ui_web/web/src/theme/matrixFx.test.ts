// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { installAppIdle } from "../utils/appIdle";
import { mountMatrixFx } from "./matrixFx";

let hidden = false;
let frames: FrameRequestCallback[] = [];

beforeEach(() => {
  hidden = false;
  frames = [];
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const ctx = { setTransform: vi.fn(), fillRect: vi.fn(), fillText: vi.fn(), fillStyle: "", font: "" };
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
  vi.stubGlobal("cancelAnimationFrame", (id: number) => { if (id) delete frames[id - 1]; });
});
afterEach(() => {
  installAppIdle()();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete (document as { hidden?: boolean }).hidden;
});

/** Runs the frames asked for so far; returns how many there were. */
function runFrames(ts: number): number {
  const due = frames.filter(Boolean);
  frames = [];
  due.forEach((cb) => cb(ts));
  return due.length;
}

it("holds the rain on its last frame while the window is in the background", () => {
  const stop = mountMatrixFx(document.createElement("div"));
  expect(runFrames(100)).toBe(1);
  hidden = true;
  document.dispatchEvent(new Event("visibilitychange"));
  // It used to redraw the whole window 30 times a second, forever.
  expect(runFrames(200)).toBe(0);
  expect(runFrames(300)).toBe(0);
  hidden = false;
  document.dispatchEvent(new Event("visibilitychange"));
  expect(runFrames(400)).toBe(1);
  stop();
  expect(runFrames(500)).toBe(0);
});
