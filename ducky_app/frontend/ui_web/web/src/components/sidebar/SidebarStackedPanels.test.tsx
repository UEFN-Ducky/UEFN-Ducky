// @vitest-environment jsdom
import { createRef } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SidebarStackedPanels } from "./SidebarStackedPanels";

vi.mock("../CtrlWheelZoomRoot", () => ({
  CtrlWheelZoomRoot: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

beforeEach(() => {
  vi.useFakeTimers();
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn(() => true);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function setup(collapsed = { a: false, b: true, c: false }) {
  const resize = vi.fn();
  const persist = vi.fn();
  const toggle = vi.fn();
  const reorder = vi.fn();
  const stackRef = createRef<HTMLDivElement>();
  const result = render(
    <SidebarStackedPanels
      order={["a", "b", "c"]}
      collapsed={collapsed}
      splitRatio={0.5}
      panelFlex={{ a: 0.3, b: 0.5, c: 0.2 }}
      stackRef={stackRef}
      panels={{ a: { title: "A", children: "one" }, b: { title: "B", children: "two" }, c: { title: "C", children: "three" } }}
      onResizeSplit={resize}
      onPersistSplit={persist}
      onToggleCollapsed={toggle}
      onSwapPanels={reorder}
    />,
  );
  vi.spyOn(stackRef.current!, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 300, 530));
  for (const [index, el] of Array.from(result.container.querySelectorAll<HTMLElement>(".sidebar-stacked-panel")).entries()) {
    const rect = [new DOMRect(0, 0, 300, 300), new DOMRect(0, 301, 300, 28), new DOMRect(0, 330, 300, 200)][index]!;
    vi.spyOn(el, "getBoundingClientRect").mockReturnValue(rect);
  }
  return { ...result, resize, persist, toggle, reorder };
}

it("keeps a resize handle on both sides of a collapsed header between open panels", () => {
  const { container } = setup();
  expect(screen.getAllByRole("separator")).toHaveLength(2);
  const panels = container.querySelectorAll<HTMLElement>(".sidebar-stacked-panel");
  expect(Number(panels[0]!.style.flexGrow)).toBeCloseTo(0.6);
  expect(Number(panels[2]!.style.flexGrow)).toBeCloseTo(0.4);
});

it("does not show unusable handles when only one panel is open", () => {
  setup({ a: false, b: true, c: true });
  expect(screen.queryAllByRole("separator")).toHaveLength(0);
});

it("captures rendered sizes at the grab point and flushes the final movement before persisting", () => {
  const { resize, persist } = setup();
  const handle = screen.getAllByRole("separator")[0]!;
  fireEvent.pointerDown(handle, { button: 0, pointerId: 1, clientY: 300 });
  expect(handle.setPointerCapture).toHaveBeenCalledWith(1);
  expect(document.body.style.cursor).toBe("row-resize");
  fireEvent.pointerMove(window, { pointerId: 1, clientY: 320 });
  act(() => vi.advanceTimersByTime(20));
  fireEvent.pointerUp(window, { pointerId: 1, clientY: 350 });
  expect(resize).toHaveBeenLastCalledWith(0, 50, 500, {
    order: ["a", "b", "c"], panelHeights: { a: 300, c: 200 },
  });
  expect(persist).toHaveBeenCalledOnce();
  expect(resize.mock.invocationCallOrder.at(-1)).toBeLessThan(persist.mock.invocationCallOrder[0]!);
  expect(document.body.style.cursor).toBe("");
});

it("cancels a header drag without toggling or reordering", () => {
  const { toggle, reorder } = setup();
  const header = screen.getByText("B").closest(".sidebar-section-header")!;
  fireEvent.pointerDown(header, { button: 0, pointerId: 1, clientX: 100, clientY: 315 });
  fireEvent.pointerMove(window, { pointerId: 1, clientX: 100, clientY: 30 });
  fireEvent.pointerCancel(window, { pointerId: 1 });
  expect(toggle).not.toHaveBeenCalled();
  expect(reorder).not.toHaveBeenCalled();
  expect(document.querySelector(".dock-panel-drag-ghost")).toBeNull();
});

it("reorders a collapsed panel at the indicated edge and ignores other pointers", () => {
  const { toggle, reorder } = setup();
  const header = screen.getByText("B").closest(".sidebar-section-header")!;
  fireEvent.pointerDown(header, { button: 0, pointerId: 1, clientX: 100, clientY: 315 });
  fireEvent.pointerUp(window, { pointerId: 2, clientX: 100, clientY: 30 });
  expect(reorder).not.toHaveBeenCalled();
  fireEvent.pointerUp(window, { pointerId: 1, clientX: 100, clientY: 30 });
  expect(reorder).toHaveBeenCalledWith("b", "a", "before");
  expect(toggle).not.toHaveBeenCalled();
});

it("still toggles a panel on a header click", () => {
  const { toggle, reorder } = setup();
  const header = screen.getByText("B").closest(".sidebar-section-header")!;
  fireEvent.pointerDown(header, { button: 0, pointerId: 1, clientX: 100, clientY: 315 });
  fireEvent.pointerUp(window, { pointerId: 1, clientX: 100, clientY: 315 });
  expect(toggle).toHaveBeenCalledWith("b");
  expect(reorder).not.toHaveBeenCalled();
});
