// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WindowResize } from "./WindowResize";

const { bounds, setBounds } = vi.hoisted(() => ({
  bounds: vi.fn().mockResolvedValue({ x: 20, y: 30, width: 800, height: 600, scale: 1 }),
  setBounds: vi.fn(),
}));
vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({ get_window_bounds: bounds, set_window_bounds: setBounds }),
}));

const bridge = vi.fn();
beforeEach(() => {
  Object.assign(window, { pywebview: { platform: "edgechromium", _jsApiCallback: bridge } });
});
afterEach(() => {
  cleanup();
  delete (window as unknown as { pywebview?: unknown }).pywebview;
  document.body.className = "";
  vi.clearAllMocks();
});

describe("native window resize", () => {
  it.each([false, true])("routes top and corner input natively (focus=%s)", (focusMode) => {
    render(<WindowResize focusMode={focusMode} />);
    expect(document.querySelectorAll(".window-resize-grip")).toHaveLength(3);
    for (const edge of ["n", "nw", "ne"]) {
      const grip = document.querySelector(`.window-resize-grip--${edge}`)!;
      fireEvent.mouseDown(grip, { button: 0, detail: 1 });
      expect(bridge).toHaveBeenLastCalledWith("uefnNativeWindowResize", [edge, false], "resize");
    }
    expect(bounds).not.toHaveBeenCalled();
    expect(setBounds).not.toHaveBeenCalled();
  });

  it("forwards a border double-click and ignores right-click", () => {
    render(<WindowResize />);
    const grip = document.querySelector(".window-resize-grip--n")!;
    fireEvent.mouseDown(grip, { button: 2, detail: 1 });
    expect(bridge).not.toHaveBeenCalled();
    fireEvent.mouseDown(grip, { button: 0, detail: 2 });
    expect(bridge).toHaveBeenCalledWith("uefnNativeWindowResize", ["n", true], "resize");
  });

  it("keeps all fallback grips on other platforms", () => {
    Object.assign(window, { pywebview: { platform: "qt" } });
    render(<WindowResize />);
    expect(document.querySelectorAll(".window-resize-grip")).toHaveLength(8);
    expect(document.body.classList.contains("native-window-chrome")).toBe(false);
  });
});
