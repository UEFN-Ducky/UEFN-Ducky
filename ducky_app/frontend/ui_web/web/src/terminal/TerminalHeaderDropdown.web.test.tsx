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

it("names the ducky that opened each terminal, and none on the person's own", () => {
  const base = { shell: "bash", active: false, running: false, runner: null };
  render(<TerminalHeaderDropdown defaultShell="bash" onShellChange={vi.fn()} onGotoTerminal={vi.fn()}
    onCloseTerminal={vi.fn()} onNewTerminal={vi.fn()} terminals={[
      { ...base, id: "terminal:a", sessionId: "a", name: "wrap-up", owner: "Fix Group Leader Moves" },
      { ...base, id: "terminal:b", sessionId: "b", name: "bash" },
    ]} />);
  press(screen.getByRole("button", { name: "2 terminals" }));
  expect(screen.getByTitle("Opened by Fix Group Leader Moves").textContent).toBe("Fix Group Leader Moves");
  expect(screen.getAllByTitle(/^Opened by/)).toHaveLength(1);
});

it("deleting a terminal keeps the menu open, so the next one can be deleted too", () => {
  const base = { shell: "bash", active: false, running: false, runner: null };
  const terms = [{ ...base, id: "terminal:a", sessionId: "a", name: "one" }, { ...base, id: "terminal:b", sessionId: "b", name: "two" }];
  function Host() {
    const [list, setList] = useState(terms);
    return <TerminalHeaderDropdown defaultShell="bash" onShellChange={vi.fn()} onGotoTerminal={vi.fn()} onNewTerminal={vi.fn()}
      onCloseTerminal={(id) => setList((l) => l.filter((t) => t.id !== id))} terminals={list} />;
  }
  render(<Host />);
  press(screen.getByRole("button", { name: "2 terminals" }));
  press(screen.getByRole("button", { name: "Delete one" }));
  expect(screen.queryByRole("button", { name: "Delete one" })).toBeNull();
  press(screen.getByRole("button", { name: "Delete two" }));
  expect(screen.getByRole("button", { name: "New terminal" })).toBeTruthy();
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
