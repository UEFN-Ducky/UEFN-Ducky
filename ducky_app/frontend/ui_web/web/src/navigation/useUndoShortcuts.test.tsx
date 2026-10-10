// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UndoHistoryProvider, useUndoHistoryOptional, type UndoHistoryValue } from "./UndoHistoryContext";
import { undoScopeFor, useUndoShortcuts } from "./useUndoShortcuts";

/** The app bridge: Ctrl+Z / Ctrl+Y drive the focused tree's history. */
function Bridge({ onReady }: { onReady: (history: UndoHistoryValue) => void }) {
  const history = useUndoHistoryOptional()!;
  useUndoShortcuts(history.undo, history.redo);
  useEffect(() => onReady(history), [history, onReady]);
  return null;
}

function App({ onReady }: { onReady: (history: UndoHistoryValue) => void }) {
  return (
    <UndoHistoryProvider>
      <Bridge onReady={onReady} />
      <div data-undo-scope="chats" tabIndex={-1} aria-label="Duckies">
        <button type="button">Ducky row</button>
        <input aria-label="Rename ducky" />
      </div>
      <div data-undo-scope="files" tabIndex={-1} aria-label="Content">
        <button type="button">File row</button>
      </div>
      <textarea aria-label="Composer" />
      <button type="button">Header button</button>
    </UndoHistoryProvider>
  );
}

afterEach(cleanup);

async function setup() {
  let history: UndoHistoryValue | null = null;
  render(<App onReady={(h) => { history = h; }} />);
  const moves = { chats: vi.fn(), files: vi.fn() };
  const redos = { chats: vi.fn(), files: vi.fn() };
  act(() => {
    history!.push("chats", { label: "Move ducky", undo: moves.chats, redo: redos.chats });
    history!.push("files", { label: "Move file", undo: moves.files, redo: redos.files });
  });
  return { moves, redos };
}

const press = (target: Element, key: string, extra: Partial<KeyboardEventInit> = {}) =>
  fireEvent.keyDown(target, { key, ctrlKey: true, ...extra });

describe("tree undo follows focus, like VS Code", () => {
  it("undoes a move only in the tree that has focus", async () => {
    const { moves } = await setup();
    const fileRow = screen.getByRole("button", { name: "File row" });
    fileRow.focus();
    await act(async () => { press(fileRow, "z"); });
    expect(moves.files).toHaveBeenCalledTimes(1);
    expect(moves.chats).not.toHaveBeenCalled(); // Content never undoes a Duckies move

    const duckRow = screen.getByRole("button", { name: "Ducky row" });
    duckRow.focus();
    await act(async () => { press(duckRow, "z"); });
    expect(moves.chats).toHaveBeenCalledTimes(1);
  });

  it("does nothing from the composer, a text field, other chrome or with nothing focused", async () => {
    const { moves } = await setup();
    const composer = screen.getByRole("textbox", { name: "Composer" });
    composer.focus();
    await act(async () => { press(composer, "z"); });
    const rename = screen.getByRole("textbox", { name: "Rename ducky" });
    rename.focus();
    await act(async () => { press(rename, "z"); });
    const header = screen.getByRole("button", { name: "Header button" });
    header.focus();
    await act(async () => { press(header, "z"); });
    (document.activeElement as HTMLElement | null)?.blur();
    expect(document.activeElement).toBe(document.body);
    await act(async () => { press(document.body, "z"); });
    expect(moves.chats).not.toHaveBeenCalled();
    expect(moves.files).not.toHaveBeenCalled();
  });

  it("redoes with Ctrl+Y and Ctrl+Shift+Z in the focused tree", async () => {
    const { moves, redos } = await setup();
    const tree = screen.getByLabelText("Duckies");
    tree.focus();
    await act(async () => { press(tree, "z"); });
    expect(moves.chats).toHaveBeenCalledTimes(1);
    await act(async () => { press(tree, "y"); });
    expect(redos.chats).toHaveBeenCalledTimes(1);
    await act(async () => { press(tree, "z"); });
    await act(async () => { press(tree, "Z", { shiftKey: true }); });
    expect(redos.chats).toHaveBeenCalledTimes(2);
    expect(redos.files).not.toHaveBeenCalled();
  });

  it("names the scope of the focused element", () => {
    render(
      <div data-undo-scope="workflows">
        <button type="button">Workflow row</button>
        <input aria-label="Folder name" />
      </div>,
    );
    expect(undoScopeFor(screen.getByRole("button", { name: "Workflow row" }))).toBe("workflows");
    expect(undoScopeFor(screen.getByRole("textbox", { name: "Folder name" }))).toBeNull();
    expect(undoScopeFor(document.body)).toBeNull();
    expect(undoScopeFor(null)).toBeNull();
  });
});
