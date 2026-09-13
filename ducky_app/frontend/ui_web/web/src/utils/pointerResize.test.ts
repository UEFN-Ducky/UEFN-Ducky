// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { pointerResize } from "./pointerResize";

let frames: Map<number, FrameRequestCallback>;
let stop: (() => void) | undefined;
beforeEach(() => {
  frames = new Map();
  let id = 0;
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => { frames.set(++id, fn); return id; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
});
afterEach(() => { stop?.(); vi.unstubAllGlobals(); });
function pointer(type: string, x: number, y = 0, pointerId = 1) {
  const event = new Event(type);
  Object.assign(event, { clientX: x, clientY: y, pointerId });
  window.dispatchEvent(event);
}
function paint() {
  const pending = [...frames.values()];
  frames.clear();
  pending.forEach((fn) => fn(0));
}

it("reduces 100 moves to one update per frame while preserving total distance and direction", () => {
  const move = vi.fn();
  stop = pointerResize({ pointerId: 1, clientX: 0, clientY: 0 }, move, vi.fn());
  for (let x = 1; x <= 100; x++) pointer("pointermove", x, x * 2);
  expect(move).not.toHaveBeenCalled();
  expect(frames.size).toBe(1);
  paint();
  expect(move.mock.calls).toEqual([[100, 200]]);
  pointer("pointermove", 90, 180);
  paint();
  expect(move.mock.calls).toEqual([[100, 200], [-10, -20]]);
});

it("applies the release position before persisting and cancels the pending frame", () => {
  let position = 0;
  const end = vi.fn(() => expect(position).toBe(55));
  const move = vi.fn((dx: number) => { position += dx; });
  stop = pointerResize({ pointerId: 1, clientX: 0, clientY: 0 }, move, end);
  pointer("pointermove", 50);
  pointer("pointerup", 55);
  expect(end).toHaveBeenCalledOnce();
  expect(frames.size).toBe(0);
  pointer("pointermove", 80);
  paint();
  expect(move).toHaveBeenCalledOnce();
});

it("ignores other pointers and releases on focus loss", () => {
  const move = vi.fn();
  const end = vi.fn();
  stop = pointerResize({ pointerId: 1, clientX: 0, clientY: 0 }, move, end);
  pointer("pointermove", 999, 0, 2);
  pointer("pointerup", 999, 0, 2);
  expect(end).not.toHaveBeenCalled();
  expect(frames.size).toBe(0);
  pointer("pointermove", 20);
  window.dispatchEvent(new Event("blur"));
  expect(move).toHaveBeenCalledWith(20, 0);
  expect(end).toHaveBeenCalledOnce();
});

it("tears down without a stale callback when the component unmounts mid-drag", () => {
  const move = vi.fn();
  const end = vi.fn();
  stop = pointerResize({ pointerId: 1, clientX: 0, clientY: 0 }, move, end);
  pointer("pointermove", 20);
  stop();
  paint();
  pointer("pointerup", 30);
  expect(move).not.toHaveBeenCalled();
  expect(end).not.toHaveBeenCalled();
});
