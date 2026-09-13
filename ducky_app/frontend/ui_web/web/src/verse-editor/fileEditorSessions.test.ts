import type { editor } from "monaco-editor";
import { expect, it, vi } from "vitest";
import { FileEditorSessions } from "./fileEditorSessions";

function model(initial: string) {
  let value = initial;
  let disposed = false;
  return {
    getValue: () => value,
    setValue: vi.fn((next: string) => { value = next; }),
    isDisposed: () => disposed,
    dispose: vi.fn(() => { disposed = true; }),
  } as unknown as editor.ITextModel;
}

it("reuses the model and view state without resetting an unsaved file on tab switches", () => {
  const sessions = new FileEditorSessions();
  const buffer = model("disk");
  const original = sessions.acquire("Folder\\A.txt", "disk", () => buffer);
  const view = {} as editor.ICodeEditorViewState;
  original.viewState = view;
  buffer.setValue("unsaved");
  const create = vi.fn();
  const restored = sessions.acquire("folder/a.txt", "external change", create);
  expect(restored).toBe(original);
  expect(restored.viewState).toBe(view);
  expect(buffer.getValue()).toBe("unsaved");
  expect(buffer.setValue).toHaveBeenCalledTimes(1);
  expect(create).not.toHaveBeenCalled();
  expect(sessions.isDirty("folder/a.txt")).toBe(true);
});

it("refreshes a clean inactive file only if disk content changed", () => {
  const sessions = new FileEditorSessions();
  const buffer = model("disk");
  sessions.acquire("a", "disk", () => buffer);
  sessions.acquire("a", "disk", vi.fn());
  expect(buffer.setValue).not.toHaveBeenCalled();
  sessions.acquire("a", "updated", vi.fn());
  expect(buffer.getValue()).toBe("updated");
  expect(sessions.isDirty("a")).toBe(false);
});

it("saves an inactive file and does not mark edits made during the write as saved", async () => {
  const sessions = new FileEditorSessions();
  const buffer = model("disk");
  sessions.acquire("a", "disk", () => buffer);
  buffer.setValue("first edit");
  let complete!: () => void;
  const write = vi.fn(() => new Promise<void>((resolve) => { complete = resolve; }));
  const saving = sessions.save("a", write);
  expect(sessions.save("a", write)).toBe(saving);
  await Promise.resolve();
  buffer.setValue("typed during save");
  complete();
  expect(await saving).toBe(false);
  expect(write).toHaveBeenCalledExactlyOnceWith("a", "first edit");
  expect(sessions.isDirty("a")).toBe(true);
  expect(await sessions.save("a", async () => {})).toBe(true);
  expect(sessions.isDirty("a")).toBe(false);
});

it("keeps failed saves dirty and allows retry", async () => {
  const sessions = new FileEditorSessions();
  const buffer = model("disk");
  sessions.acquire("a", "disk", () => buffer);
  buffer.setValue("edit");
  await expect(sessions.save("a", async () => { throw Error("disk full"); })).rejects.toThrow("disk full");
  expect(sessions.isDirty("a")).toBe(true);
  expect(await sessions.save("a", async () => {})).toBe(true);
});

it("releases closed files and all project models on disposal", () => {
  const sessions = new FileEditorSessions();
  const first = model("a");
  const second = model("b");
  sessions.acquire("a", "a", () => first);
  sessions.acquire("b", "b", () => second);
  sessions.retain(["B"]);
  expect(first.isDisposed()).toBe(true);
  expect(second.isDisposed()).toBe(false);
  expect(sessions.get("a")).toBeUndefined();
  sessions.dispose();
  expect(second.isDisposed()).toBe(true);
});
