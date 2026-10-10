// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createScopedUndoHistory, type UndoableAction } from "./scopedUndoHistory";

/** A move that tracks where "row" is, like a tree move. */
function moveAction(state: { at: string }, from: string, to: string): UndoableAction {
  return {
    label: `Move to ${to}`,
    undo: () => {
      state.at = from;
    },
    redo: () => {
      state.at = to;
    },
  };
}

afterEach(() => vi.restoreAllMocks());

describe("tree undo history", () => {
  it("undoes a move, redoes it, and a new move clears redo", async () => {
    const history = createScopedUndoHistory();
    const row = { at: "B" };
    history.push("chats", moveAction(row, "A", "B"));
    expect(history.canUndo("chats")).toBe(true);
    expect(await history.undo("chats")).toBe(true);
    expect(row.at).toBe("A");
    expect(history.canRedo("chats")).toBe(true);
    expect(await history.redo("chats")).toBe(true);
    expect(row.at).toBe("B");
    expect(await history.undo("chats")).toBe(true);
    // A new move after an undo drops the redo branch (VS Code).
    row.at = "C";
    history.push("chats", moveAction(row, "A", "C"));
    expect(history.canRedo("chats")).toBe(false);
    expect(await history.redo("chats")).toBe(false);
    expect(row.at).toBe("C");
  });

  it("keeps a separate history per tree", async () => {
    const history = createScopedUndoHistory();
    const duck = { at: "B" };
    const file = { at: "Y" };
    history.push("chats", moveAction(duck, "A", "B"));
    history.push("files", moveAction(file, "X", "Y"));
    expect(await history.undo("files")).toBe(true);
    expect(file.at).toBe("X");
    expect(duck.at).toBe("B"); // Content undo never touched the Duckies move
    expect(history.canUndo("files")).toBe(false);
    expect(history.canUndo("chats")).toBe(true);
    // A new Duckies move does not clear Content's redo.
    history.push("chats", moveAction(duck, "B", "C"));
    expect(history.canRedo("files")).toBe(true);
    expect(await history.undo("workflows")).toBe(false);
  });

  it("leaves a failed undo in place so it can be tried again", async () => {
    const history = createScopedUndoHistory();
    let fail = true;
    history.push("files", {
      label: "Move",
      undo: async () => {
        if (fail) throw new Error("host busy");
      },
      redo: () => {},
    });
    expect(await history.undo("files")).toBe(false);
    expect(history.canUndo("files")).toBe(true);
    fail = false;
    expect(await history.undo("files")).toBe(true);
    expect(history.canRedo("files")).toBe(true);
  });

  it("keeps at most the newest actions", async () => {
    const history = createScopedUndoHistory(2);
    const row = { at: "0" };
    history.push("chats", moveAction(row, "0", "1"));
    history.push("chats", moveAction(row, "1", "2"));
    history.push("chats", moveAction(row, "2", "3"));
    row.at = "3";
    expect(await history.undo("chats")).toBe(true);
    expect(await history.undo("chats")).toBe(true);
    expect(await history.undo("chats")).toBe(false);
    expect(row.at).toBe("1");
  });

  it("lives in memory only: nothing is stored, and a new session starts empty", async () => {
    // localStorage and sessionStorage both write through Storage.prototype.setItem.
    const storageSet = vi.spyOn(Storage.prototype, "setItem");
    const history = createScopedUndoHistory();
    const row = { at: "B" };
    history.push("chats", moveAction(row, "A", "B"));
    await history.undo("chats");
    await history.redo("chats");
    expect(storageSet).not.toHaveBeenCalled();
    // The app closing is a fresh history next time.
    expect(createScopedUndoHistory().canUndo("chats")).toBe(false);
    // And the module itself never reaches for storage, a database or the host.
    for (const file of ["./scopedUndoHistory.ts", "./UndoHistoryContext.tsx"]) {
      const source = readFileSync(fileURLToPath(new URL(file, import.meta.url)), "utf8");
      expect(source).not.toMatch(/localStorage|sessionStorage|indexedDB|getApi|fetch\(/);
    }
  });
});
