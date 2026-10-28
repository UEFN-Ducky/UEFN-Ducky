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
  await Promise.resolve();
  buffer.setValue("typed during save");
  complete();
  expect(await saving).toBe(false);
  expect(write).toHaveBeenCalledExactlyOnceWith("a", "first edit");
  expect(sessions.isDirty("a")).toBe(true);
  expect(await sessions.save("a", async () => {})).toBe(true);
  expect(sessions.isDirty("a")).toBe(false);
});

it("coalesces 20 Ctrl+S during a slow write into one trailing write of the final text", async () => {
  const sessions = new FileEditorSessions();
  const buffer = model("disk");
  sessions.acquire("a", "disk", () => buffer);
  const write = vi.fn(() => new Promise<void>((resolve) => setTimeout(resolve, 5)));
  const presses: Promise<boolean>[] = [];
  for (let i = 0; i < 20; i += 1) {
    buffer.setValue(`edit ${i}`);
    presses.push(sessions.save("a", write));
  }
  const results = await Promise.all(presses);
  expect(write.mock.calls.length).toBeLessThanOrEqual(4);
  expect(write).toHaveBeenLastCalledWith("a", "edit 19");
  expect(results.at(-1)).toBe(true);
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

it("skips the full read on a tab switch while the file is unchanged on disk", async () => {
  const sessions = new FileEditorSessions();
  let stat = { exists: true, mtime_ns: 1, size: 4 };
  const statFn = vi.fn(async () => stat);
  const read = vi.fn(async () => ({ content: "disk", path: "Content/A.verse" }));
  const first = await sessions.readForMount("a", read, statFn);
  sessions.acquire("a", first.content, () => model(first.content));
  expect(read).toHaveBeenCalledTimes(1);

  expect(await sessions.readForMount("a", read, statFn)).toEqual({ content: "disk", path: "Content/A.verse" });
  expect(read).toHaveBeenCalledTimes(1);

  stat = { exists: true, mtime_ns: 2, size: 5 };
  await sessions.readForMount("a", read, statFn);
  expect(read).toHaveBeenCalledTimes(2);
});

it("switching between two unchanged tabs 50 times creates 2 models and reads each file once", async () => {
  const sessions = new FileEditorSessions();
  const create = vi.fn((text: string) => model(text));
  const read = vi.fn(async (p: string) => ({ content: `text of ${p}`, path: p }));
  const statFn = async (p: string) => ({ exists: true, mtime_ns: p.length, size: 10 });
  for (let i = 0; i < 50; i += 1) {
    const p = i % 2 ? "Content/b.verse" : "Content/a.json";
    const { content } = await sessions.readForMount(p, read, statFn);
    sessions.acquire(p, content, () => create(content));
  }
  expect(create).toHaveBeenCalledTimes(2);
  expect(read).toHaveBeenCalledTimes(2);
});

it("always reads when there is no session or the stat fails", async () => {
  const sessions = new FileEditorSessions();
  const statFn = vi.fn(async () => ({ exists: true, mtime_ns: 1, size: 4 }));
  const read = vi.fn(async () => ({ content: "disk", path: "a" }));
  await sessions.readForMount("a", read, statFn);
  await sessions.readForMount("a", read, statFn);
  expect(read).toHaveBeenCalledTimes(2);

  sessions.acquire("a", "disk", () => model("disk"));
  await sessions.readForMount("a", read, async () => { throw Error("gone"); });
  expect(read).toHaveBeenCalledTimes(3);
});

it("recognises the watcher's stat after our own save as an echo", async () => {
  const sessions = new FileEditorSessions();
  const buffer = model("disk");
  sessions.acquire("a", "disk", () => buffer);
  buffer.setValue("edit");
  await sessions.save("a", async () => ({ path: "a", mtime_ns: 7, size: 4 }));
  expect(sessions.isDiskEcho("a", { exists: true, mtime_ns: 7, size: 4 })).toBe(true);
  expect(sessions.isDiskEcho("a", { exists: true, mtime_ns: 8, size: 4 })).toBe(false);
  // The next tab switch trusts the saved stat as well: no full read.
  const read = vi.fn(async () => ({ content: "x", path: "a" }));
  expect((await sessions.readForMount("a", read, async () => ({ exists: true, mtime_ns: 7, size: 4 }))).content).toBe("edit");
  expect(read).not.toHaveBeenCalled();
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
