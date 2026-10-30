// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DropdownPanel } from "./DropdownPanel";

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

  it("a pointerdown inside the panel does not close it; outside does", () => {
    const onClose = vi.fn();
    render(<Menu onClose={onClose} />);
    fireEvent.pointerDown(screen.getByText("item"));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.pointerDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
