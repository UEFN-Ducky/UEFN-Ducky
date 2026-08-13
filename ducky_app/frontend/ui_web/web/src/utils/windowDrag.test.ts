import { afterEach, describe, expect, it } from "vitest";
import { beginNativeWindowMove } from "./windowDrag";

describe("beginNativeWindowMove", () => {
  afterEach(() => {
    delete (globalThis as { window?: unknown }).window;
  });

  it("posts uefnNativeWindowMove through the pywebview sync bridge", () => {
    const calls: unknown[][] = [];
    (globalThis as { window: unknown }).window = {
      pywebview: {
        _jsApiCallback: (...args: unknown[]) => {
          calls.push(args);
        },
      },
    };
    expect(beginNativeWindowMove(640, 80)).toBe(true);
    expect(calls).toEqual([["uefnNativeWindowMove", [640, 80], "move"]]);
  });

  it("returns false when the bridge is missing", () => {
    (globalThis as { window: unknown }).window = {};
    expect(beginNativeWindowMove(1, 2)).toBe(false);
  });
});
