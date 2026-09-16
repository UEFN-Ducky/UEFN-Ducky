// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WindowDrag } from "./WindowDrag";
import { WindowResize } from "./WindowResize";

const { bounds, setBounds, toggleMaximize, remote } = vi.hoisted(() => ({
  bounds: vi.fn().mockResolvedValue({ x: 20, y: 30, width: 800, height: 600, scale: 1 }),
  setBounds: vi.fn(),
  toggleMaximize: vi.fn(),
  remote: vi.fn(() => false),
}));
vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({ get_window_bounds: bounds, set_window_bounds: setBounds, toggle_maximize: toggleMaximize }),
  isRemote: remote,
}));

const bridge = vi.fn();
function mountCaption() {
  return render(<>
    <WindowDrag />
    <WindowResize />
    <header className="app-header drag-region app-drag-surface">
      <div data-testid="left"><span>Project</span></div>
      <div className="app-header-center drag-region app-drag-surface" data-testid="center" />
      <div data-testid="right" />
      <button data-testid="button"><span>Search</span></button>
      <div className="no-drag" data-testid="control"><span>Control</span></div>
    </header>
    <div className="focus-drag-bar" data-testid="focus" />
  </>);
}

// MouseEvent supplies coordinates/buttons in jsdom, which has no PointerEvent constructor.
function pointer(target: EventTarget, type: string, screenX = 100, screenY = 24, buttons = 1) {
  const event = new MouseEvent(type, { bubbles: true, button: 0, buttons, screenX, screenY });
  Object.defineProperty(event, "pointerType", { value: "mouse" });
  target.dispatchEvent(event);
}

beforeEach(() => {
  Object.assign(window, { pywebview: { platform: "edgechromium", _jsApiCallback: bridge } });
  remote.mockReturnValue(false);
});
afterEach(() => {
  cleanup();
  delete (window as unknown as { pywebview?: unknown }).pywebview;
  document.body.className = "";
  vi.clearAllMocks();
});

describe("Windows header input", () => {
  it("starts dragging when the desktop bridge arrives after React mounts", () => {
    delete (window as unknown as { pywebview?: unknown }).pywebview;
    remote.mockReturnValue(true);
    const { getByTestId } = mountCaption();
    expect(document.body.classList.contains("native-caption-input-ready")).toBe(false);
    Object.assign(window, { pywebview: { platform: "edgechromium", _jsApiCallback: bridge } });
    remote.mockReturnValue(false);
    act(() => { window.dispatchEvent(new Event("pywebviewready")); });
    pointer(getByTestId("center"), "pointerdown");
    pointer(window, "pointermove", 120, 24);
    expect(bridge.mock.calls).toEqual([["uefnNativeWindowMove", [120, 24], "move"]]);
    expect(document.body.classList.contains("native-caption-input-ready")).toBe(true);
    fireEvent.doubleClick(getByTestId("center"));
    expect(toggleMaximize).toHaveBeenCalledTimes(1);
  });

  it("handles repeated bridge-ready events once and restores native captions on cleanup", () => {
    const { getByTestId, unmount } = mountCaption();
    act(() => {
      window.dispatchEvent(new Event("pywebviewready"));
      window.dispatchEvent(new Event("pywebviewready"));
    });
    expect(document.body.classList.contains("native-caption-input-ready")).toBe(true);
    fireEvent.doubleClick(getByTestId("center"));
    expect(toggleMaximize).toHaveBeenCalledTimes(1);
    unmount();
    expect(document.body.classList.contains("native-caption-input-ready")).toBe(false);
  });

  it.each(["left", "center", "right"])("hands movement from %s to Windows once, after pointer movement", (region) => {
    const { getByTestId } = mountCaption();
    pointer(getByTestId(region), "pointerdown");
    pointer(window, "pointermove", 102, 25);
    expect(bridge).not.toHaveBeenCalled();
    pointer(window, "pointermove", 104, 24);
    pointer(window, "pointermove", 120, 24);
    expect(bridge.mock.calls).toEqual([["uefnNativeWindowMove", [104, 24], "move"]]);
    expect(bounds).not.toHaveBeenCalled();
    expect(setBounds).not.toHaveBeenCalled();
  });

  it.each(["left", "center", "right"])("maximizes and restores on stationary double-clicks in %s", (region) => {
    const { getByTestId } = mountCaption();
    const target = getByTestId(region);
    for (let i = 0; i < 2; i++) {
      pointer(target, "pointerdown");
      pointer(window, "pointerup", 100, 24, 0);
    }
    fireEvent.doubleClick(target);
    expect(toggleMaximize).toHaveBeenCalledTimes(1);
    expect(bridge).not.toHaveBeenCalled();
  });

  it.each(["pointerup", "pointercancel", "blur", "released"])("cancels pending movement on %s", (ending) => {
    const { getByTestId } = mountCaption();
    pointer(getByTestId("center"), "pointerdown");
    if (ending === "released") pointer(window, "pointermove", 101, 24, 0);
    else window.dispatchEvent(new Event(ending));
    pointer(window, "pointermove", 120, 24);
    expect(bridge).not.toHaveBeenCalled();
  });

  it.each(["button", "control"])("leaves %s input to the control, including nested icons", (control) => {
    const { getByTestId } = mountCaption();
    const target = getByTestId(control).firstElementChild!;
    pointer(target, "pointerdown");
    pointer(window, "pointermove", 130, 24);
    fireEvent.doubleClick(target);
    expect(fireEvent.contextMenu(target)).toBe(true);
    expect(toggleMaximize).not.toHaveBeenCalled();
    expect(bridge).not.toHaveBeenCalled();
  });

  it("routes the top edge to native resize without starting a caption move", () => {
    mountCaption();
    const grip = document.querySelector(".window-resize-grip--n")!;
    pointer(grip, "pointerdown", 600, 2);
    fireEvent.mouseDown(grip, { button: 0 });
    pointer(window, "pointermove", 620, 2);
    fireEvent.doubleClick(grip);
    expect(bridge.mock.calls).toEqual([["uefnNativeWindowResize", ["n", false], "resize"]]);
    expect(toggleMaximize).not.toHaveBeenCalled();
  });

  it("opens the native system menu from blank header space", () => {
    const { getByTestId } = mountCaption();
    expect(fireEvent.contextMenu(getByTestId("center"))).toBe(false);
    expect(bridge.mock.calls).toEqual([["uefnNativeWindowMenu", [], "window-menu"]]);
  });

  it("preserves the native fallback for other caption surfaces", () => {
    const { getByTestId } = mountCaption();
    pointer(getByTestId("focus"), "pointerdown");
    fireEvent.doubleClick(getByTestId("focus"));
    expect(bridge.mock.calls).toEqual([["uefnNativeWindowMove", [100, 24], "move"]]);
    expect(toggleMaximize).not.toHaveBeenCalled();
  });

  it("keeps the standalone Settings header's drag filler working", () => {
    const { container } = render(<>
      <WindowDrag />
      <header className="app-header app-header--settings">
        <button className="no-drag">Back</button>
        <div className="app-header-drag-fill drag-region app-drag-surface" />
      </header>
    </>);
    const fill = container.querySelector(".app-header-drag-fill")!;
    fireEvent.doubleClick(fill);
    pointer(fill, "pointerdown");
    pointer(window, "pointermove", 120, 24);
    expect(toggleMaximize).toHaveBeenCalledTimes(1);
    expect(bridge.mock.calls).toEqual([["uefnNativeWindowMove", [120, 24], "move"]]);
  });

  it("removes pending drag listeners when the component unmounts", () => {
    const { getByTestId, unmount } = mountCaption();
    pointer(getByTestId("center"), "pointerdown");
    unmount();
    pointer(window, "pointermove", 120, 24);
    expect(bridge).not.toHaveBeenCalled();
  });

  it("does not control the host window from the remote panel", () => {
    remote.mockReturnValue(true);
    const { getByTestId } = mountCaption();
    pointer(getByTestId("center"), "pointerdown");
    pointer(window, "pointermove", 120, 24);
    fireEvent.doubleClick(getByTestId("center"));
    fireEvent.contextMenu(getByTestId("center"));
    expect(toggleMaximize).not.toHaveBeenCalled();
    expect(bridge).not.toHaveBeenCalled();
  });
});
