// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DropdownPanel, MENU_POP_MS, popOrigin } from "./DropdownPanel";

function Menu({ zIndex, onClose = () => {} }: { zIndex?: number; onClose?: () => void }) {
  const anchor = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={anchor}>open</button>
      <DropdownPanel anchorRef={anchor} open onClose={onClose} zIndex={zIndex}>
        <button>item</button>
      </DropdownPanel>
    </>
  );
}

afterEach(cleanup);

describe("DropdownPanel stacking", () => {
  it("passes its zIndex through the variable the panel's own rule reads", () => {
    render(<Menu zIndex={2147483300} />);
    const css = [...document.querySelectorAll("style")].map((s) => s.textContent).join("\n");
    expect(css).toContain("--dropdown-z: 2147483300");
    // A plain z-index on the scoped class loses to `.dropdown-panel.is-positioned`: menus in the
    // setup opened under it. The positioned rule must read the variable instead.
    const sheet = readFileSync(join(process.cwd(), "src/theme/styles/components-inline.css"), "utf8");
    expect(sheet).toMatch(/\.dropdown-panel\.is-positioned \{[^}]*z-index: var\(--dropdown-z, 100010\)/);
  });
});

describe("DropdownPanel closes on any outside interaction", () => {
  it("closes when the window blurs (a click inside a plugin iframe)", () => {
    const onClose = vi.fn();
    render(<Menu onClose={onClose} />);
    fireEvent.blur(window);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("stays open when focus moved into the panel itself", () => {
    const onClose = vi.fn();
    render(<Menu onClose={onClose} />);
    screen.getByText("item").focus();
    fireEvent.blur(window);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<Menu onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("while open, window caption surfaces give their clicks to the page", () => {
    const view = render(<Menu />);
    const second = render(<Menu />);
    expect(document.body.classList.contains("dropdown-open")).toBe(true);
    view.unmount();
    // Another menu is still open.
    expect(document.body.classList.contains("dropdown-open")).toBe(true);
    second.unmount();
    expect(document.body.classList.contains("dropdown-open")).toBe(false);
    const sheet = readFileSync(join(process.cwd(), "src/theme/styles/global.css"), "utf8");
    const rule = sheet.match(/body\.dropdown-open \.drag-region,[^{]*\{[^}]*\}/)?.[0] || "";
    for (const sel of [".app-header", ".focus-drag-bar", ".app-drag-surface"]) expect(rule).toContain(`body.dropdown-open ${sel}`);
    expect(rule).toContain("-webkit-app-region: no-drag");
  });

  it("a pointerdown inside the panel does not close it; outside does", () => {
    const onClose = vi.fn();
    render(<Menu onClose={onClose} />);
    fireEvent.pointerDown(screen.getByText("item"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.pointerDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("DropdownPanel zooms out of its anchor and back into it", () => {
  function Toggle({ open }: { open: boolean }) {
    const anchor = useRef<HTMLButtonElement>(null);
    return (
      <>
        <button ref={anchor}>open</button>
        <DropdownPanel anchorRef={anchor} open={open} onClose={() => {}}>
          <button>item</button>
        </DropdownPanel>
      </>
    );
  }

  it("stays mounted as is-closing for the zoom back, then unmounts", () => {
    vi.useFakeTimers();
    // jsdom has no animations; give it the API so the close animation runs like in the app.
    (Element.prototype as unknown as { getAnimations: () => [] }).getAnimations = () => [];
    try {
      const view = render(<Toggle open />);
      expect(document.querySelector(".dropdown-panel.menu-pop")).toBeTruthy();
      view.rerender(<Toggle open={false} />);
      expect(document.querySelector(".dropdown-panel.menu-pop.is-closing")).toBeTruthy();
      act(() => vi.advanceTimersByTime(MENU_POP_MS));
      expect(document.querySelector(".dropdown-panel")).toBeNull();
    } finally {
      delete (Element.prototype as unknown as { getAnimations?: unknown }).getAnimations;
      vi.useRealTimers();
    }
  });

  it("puts the zoom's origin on the trigger's center", () => {
    const trigger = { getBoundingClientRect: () => ({ left: 100, top: 10, width: 20, height: 20 }) } as unknown as Element;
    expect(popOrigin(trigger, { left: 40, top: 36 })).toBe("70px -16px");
    expect(popOrigin(null, { left: 0, top: 0 })).toBeUndefined();
  });
});
