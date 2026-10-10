// @vitest-environment jsdom
import { useRef, useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ChoiceDropdown } from "../components/ChoiceDropdown";
import { DropdownPanel } from "../components/DropdownPanel";
import { TerminalHeaderDropdown } from "./TerminalHeaderDropdown";

afterEach(cleanup);

// Include capture-phase pointerdown and native mousedown before the click.
function press(element: Element) {
  fireEvent.pointerDown(element);
  fireEvent.mouseDown(element);
  fireEvent.pointerUp(element);
  fireEvent.mouseUp(element);
  fireEvent.click(element);
}

it("selects bash without dismissing the terminal menu and closes both menus outside", () => {
  const onShellChange = vi.fn();
  render(<TerminalHeaderDropdown terminals={[]} defaultShell="powershell" onShellChange={onShellChange}
    onGotoTerminal={vi.fn()} onCloseTerminal={vi.fn()} onNewTerminal={vi.fn()} />);
  press(screen.getByRole("button", { name: "Terminals" }));
  press(screen.getByRole("button", { name: "Default shell" }));
  press(screen.getByRole("radio", { name: "bash" }));
  expect(onShellChange).toHaveBeenCalledExactlyOnceWith("bash");
  expect(screen.getByRole("button", { name: "New terminal" })).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull();
  press(screen.getByRole("button", { name: "Default shell" }));
  expect(screen.getByRole("dialog")).toBeTruthy();
  press(document.body);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("button", { name: "New terminal" })).toBeNull();
});

it.each(["radio", "checkbox"] as const)("keeps a portaled %s choice inside a DropdownPanel usable", (mode) => {
  const changed = vi.fn();
  function Nested() {
    const anchorRef = useRef<HTMLButtonElement>(null);
    const [open, setOpen] = useState(true);
    return <><button ref={anchorRef}>Parent</button>
      <DropdownPanel open={open} anchorRef={anchorRef} onClose={() => setOpen(false)}>
        <span>Parent contents</span>
        {mode === "radio" ? <ChoiceDropdown aria-label="Nested choice" value="a" onChange={changed}
          options={[{ value: "a", label: "A" }, { value: "b", label: "B" }]} /> :
          <ChoiceDropdown aria-label="Nested choice" mode="checkbox" values={[]} onChange={changed}
            options={[{ value: "b", label: "B" }]} />}
      </DropdownPanel></>;
  }
  render(<Nested />);
  press(screen.getByRole("button", { name: "Nested choice" }));
  press(screen.getByRole(mode, { name: "B" }));
  expect(changed).toHaveBeenCalledExactlyOnceWith(mode === "radio" ? "b" : ["b"]);
  expect(screen.getByText("Parent contents")).toBeTruthy();
  if (mode === "radio") press(screen.getByRole("button", { name: "Nested choice" }));
  press(document.body);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByText("Parent contents")).toBeNull();
});
