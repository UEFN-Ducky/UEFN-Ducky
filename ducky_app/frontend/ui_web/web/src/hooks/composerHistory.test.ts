import { afterEach, describe, expect, it } from "vitest";
import type { MessageAttachmentDto } from "../types/panel";
import {
  _resetComposerHistoryForTests,
  composerHistoryKey,
  recordComposer,
  redoComposer,
  undoComposer,
} from "./composerHistory";

const image: MessageAttachmentDto = { kind: "image", name: "shot.png", mime: "image/png", data_base64: "iVBORw0KGgo=" };

/** Types `text` one character at a time, 100 ms apart, starting at `t`. Returns the end time. */
function type(chat: string, from: string, text: string, t: number, attachments: MessageAttachmentDto[] = []): number {
  let value = from;
  for (const ch of text) {
    value += ch;
    t += 100;
    recordComposer(chat, value, value.length, attachments, t);
  }
  return t;
}

afterEach(() => _resetComposerHistoryForTests());

describe("composer history", () => {
  it("undoes typing a word at a time and redoes it", () => {
    recordComposer("c", "", 0, [], 0);
    type("c", "", "fix the bar", 0);
    expect(undoComposer("c")?.text).toBe("fix the");
    expect(undoComposer("c")?.text).toBe("fix");
    expect(undoComposer("c")?.text).toBe("");
    expect(undoComposer("c")).toBeNull();
    expect(redoComposer("c")?.text).toBe("fix");
    expect(redoComposer("c")?.text).toBe("fix the");
  });

  it("keeps a paste and an attachment as steps of their own, restoring caret and images", () => {
    recordComposer("c", "", 0, [], 0);
    let t = type("c", "", "see", 0);
    recordComposer("c", "see this log", 12, [], (t += 100)); // paste
    recordComposer("c", "see this log", 12, [image], (t += 100)); // attach an image
    let step = undoComposer("c")!;
    expect([step.text, step.attachments]).toEqual(["see this log", []]);
    step = undoComposer("c")!;
    expect([step.text, step.caret]).toEqual(["see", 3]);
    step = redoComposer("c")!;
    step = redoComposer("c")!;
    expect(step.attachments).toEqual([image]);
  });

  it("brings a sent prompt back with its attachments", () => {
    recordComposer("c", "", 0, [], 0);
    recordComposer("c", "ship it", 7, [image], 5000);
    recordComposer("c", "", 0, [], 9000); // sent: the composer clears
    const step = undoComposer("c")!;
    expect([step.text, step.attachments]).toEqual(["ship it", [image]]);
  });

  it("drops the redo steps once something new is typed", () => {
    recordComposer("c", "", 0, [], 0);
    type("c", "", "one two", 0);
    undoComposer("c");
    recordComposer("c", "one!", 4, [], 10_000);
    expect(redoComposer("c")).toBeNull();
    expect(undoComposer("c")?.text).toBe("one");
  });

  it("keeps each chat's history apart and ignores caret-only moves", () => {
    recordComposer("a", "", 0, [], 0);
    recordComposer("b", "", 0, [], 0);
    type("a", "", "alpha", 0);
    type("b", "", "beta", 0);
    recordComposer("a", "alpha", 2, [], 5000); // caret moved, no new step
    expect(undoComposer("a")?.text).toBe("");
    expect(undoComposer("b")?.text).toBe("");
    expect(undoComposer("a")).toBeNull();
  });

  it("treats only Ctrl/Cmd+Z, Ctrl/Cmd+Shift+Z and Ctrl/Cmd+Y as history keys", () => {
    const k = (key: string, mods: Partial<{ ctrlKey: boolean; metaKey: boolean; shiftKey: boolean; altKey: boolean }> = {}) =>
      composerHistoryKey({ key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods });
    expect(k("z", { ctrlKey: true })).toBe("undo");
    expect(k("Z", { ctrlKey: true, shiftKey: true })).toBe("redo");
    expect(k("y", { metaKey: true })).toBe("redo");
    expect(k("z")).toBeNull();
    expect(k("z", { ctrlKey: true, altKey: true })).toBeNull();
    expect(k("a", { ctrlKey: true })).toBeNull();
  });
});
