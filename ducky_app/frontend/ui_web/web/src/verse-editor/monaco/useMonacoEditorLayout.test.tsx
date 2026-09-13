// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import type { editor } from "monaco-editor";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useMonacoEditorLayout } from "./useMonacoEditorLayout";

let resize: () => void;
let frames: Map<number, FrameRequestCallback>;
const disconnect = vi.fn();
beforeEach(() => {
  vi.useFakeTimers();
  frames = new Map();
  let id = 0;
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => { frames.set(++id, fn); return id; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: () => void) { resize = callback; }
    observe() {}
    disconnect = disconnect;
  });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.clearAllMocks(); });
function paint() {
  act(() => {
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach((fn) => fn(0));
  });
}

it("lays out once for a burst, skips unchanged sizes and settles without another layout", () => {
  let width = 800;
  const container = document.createElement("div");
  Object.defineProperties(container, { clientWidth: { get: () => width }, clientHeight: { get: () => 600 } });
  const layout = vi.fn();
  const editorRef = { current: { layout, getModel: () => null } as unknown as editor.IStandaloneCodeEditor };
  const containerRef = { current: container };
  const { result, unmount } = renderHook(() => useMonacoEditorLayout(containerRef, editorRef, true));
  paint();
  expect(layout).toHaveBeenLastCalledWith({ width: 800, height: 600 });
  width = 900;
  for (let i = 0; i < 100; i++) resize();
  expect(frames.size).toBe(1);
  paint();
  expect(layout).toHaveBeenCalledTimes(2);
  resize(); paint();
  expect(layout).toHaveBeenCalledTimes(2);
  act(() => vi.advanceTimersByTime(200));
  expect(result.current).toBe(false);
  expect(layout).toHaveBeenCalledTimes(2);
  width = 0; resize(); paint();
  expect(layout).toHaveBeenCalledTimes(2);
  width = 900; resize(); paint();
  expect(layout).toHaveBeenCalledTimes(3);
  resize(); unmount();
  expect(frames.size).toBe(0);
  expect(disconnect).toHaveBeenCalled();
});
