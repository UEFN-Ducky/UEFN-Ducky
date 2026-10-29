// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ContextMenu, type ContextMenuItem } from "./ContextMenu";

afterEach(cleanup);

it("shows each row's icon and keeps a long menu inside the window", () => {
  const items: ContextMenuItem[] = Array.from({ length: 60 }, (_, i) => ({
    id: `b${i}`,
    label: `Button ${i}`,
    icon: <svg data-testid={`icon-${i}`} />,
    switch: true,
    checked: i % 2 === 0,
  }));
  render(<ContextMenu x={10} y={10} items={items} onClose={() => {}} />);
  expect(document.querySelectorAll(".context-menu-icon")).toHaveLength(60);
  expect(document.querySelector("[data-testid=icon-59]")).toBeTruthy();
  // 60 rows don't fit: the menu is capped to the window and scrolls (style comes from CSS).
  const css = Array.from(document.querySelectorAll("style")).map((s) => s.textContent).join("");
  expect(css).toContain(`--context-menu-max-height: ${Math.max(120, window.innerHeight - 16)}px`);
});

it("opens upward from a pointer near the bottom, sized by the drawn menu", () => {
  const rect = HTMLElement.prototype.getBoundingClientRect;
  // Rows drawn taller than the 32 px guess: 7 rows come out 300 px.
  HTMLElement.prototype.getBoundingClientRect = function () {
    return this.classList.contains("context-menu") ? ({ width: 240, height: 300 } as DOMRect) : rect.call(this);
  };
  try {
    const y = window.innerHeight - 20;
    const items: ContextMenuItem[] = Array.from({ length: 7 }, (_, i) => ({ id: `b${i}`, label: `B${i}`, switch: true }));
    render(<ContextMenu x={10} y={y} items={items} onClose={() => {}} />);
    const css = Array.from(document.querySelectorAll("style")).map((s) => s.textContent).join("");
    expect(css).toContain(`--context-menu-top: ${y - 300}px`);
  } finally {
    HTMLElement.prototype.getBoundingClientRect = rect;
  }
});
