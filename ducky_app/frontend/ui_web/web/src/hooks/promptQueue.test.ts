import { describe, expect, it, beforeEach } from "vitest";
import {
  appendPrompt,
  removePrompt,
  updatePromptText,
  movePromptToFront,
  shiftPrompt,
  makeQueuedPrompt,
  enqueuePrompt,
  takeNextPrompt,
  takePromptForEdit,
  subscribePromptQueue,
  takeNextPromptForDrain,
  releasePromptDrainLock,
  isPromptDrainLocked,
  getPromptQueue,
  _resetPromptQueuesForTests,
  type QueuedPrompt,
} from "./promptQueue";

function q(text: string, id: string): QueuedPrompt {
  return makeQueuedPrompt(text, { mode: "agent", model: "m", id })!;
}

describe("promptQueue helpers", () => {
  it("appends and shifts FIFO", () => {
    const a = q("first", "a");
    const b = q("second", "b");
    const items = appendPrompt(appendPrompt([], a), b);
    expect(shiftPrompt(items)).toEqual({ next: a, rest: [b] });
  });

  it("edit / delete / move-to-front", () => {
    const items = [q("one", "a"), q("two", "b"), q("three", "c")];
    expect(updatePromptText(items, "b", "  two-edited  ").map((p) => p.text)).toEqual([
      "one",
      "two-edited",
      "three",
    ]);
    expect(updatePromptText(items, "b", "   ").map((p) => p.id)).toEqual(["a", "c"]);
    expect(removePrompt(items, "b").map((p) => p.id)).toEqual(["a", "c"]);
    expect(movePromptToFront(items, "c").map((p) => p.id)).toEqual(["c", "a", "b"]);
    expect(movePromptToFront(items, "a")).toBe(items);
  });

  it("makeQueuedPrompt rejects blank", () => {
    expect(makeQueuedPrompt("  ", { mode: "agent", model: "m" })).toBeNull();
  });

  it("makeQueuedPrompt allows image-only", () => {
    const item = makeQueuedPrompt("", {
      mode: "agent",
      model: "m",
      attachments: [{ kind: "image", name: "shot.png", mime: "image/png", data_base64: "x" }],
    });
    expect(item).not.toBeNull();
    expect(item!.text).toBe("");
    expect(item!.attachments).toHaveLength(1);
  });

  it("updatePromptText keeps image-only when text cleared", () => {
    const items = [
      makeQueuedPrompt("caption", {
        mode: "agent",
        model: "m",
        id: "img",
        attachments: [{ kind: "image", name: "shot.png", mime: "image/png", data_base64: "x" }],
      })!,
    ];
    expect(updatePromptText(items, "img", "   ")).toEqual([
      expect.objectContaining({ id: "img", text: "", attachments: expect.any(Array) }),
    ]);
  });
});

describe("promptQueue store", () => {
  beforeEach(() => _resetPromptQueuesForTests());

  it("removes an edited prompt before notifying subscribers so it cannot auto-send", () => {
    const edited = makeQueuedPrompt("revise me", {
      id: "edit",
      mode: "plan",
      model: "queued-model",
      attachments: [{ kind: "file", name: "notes.txt", text: "keep this" }],
    })!;
    const remaining = q("still queued", "next");
    enqueuePrompt("c1", edited);
    enqueuePrompt("c1", remaining);
    enqueuePrompt("c2", q("other chat", "edit"));
    const observed: QueuedPrompt[][] = [];
    subscribePromptQueue("c1", () => observed.push(getPromptQueue("c1")));

    expect(takePromptForEdit("c1", "edit")).toBe(edited);
    expect(observed).toEqual([[remaining]]);
    expect(takePromptForEdit("c1", "edit")).toBeNull();
    expect(takeNextPromptForDrain("c1")).toBe(remaining);
    releasePromptDrainLock("c1");
    expect(takeNextPromptForDrain("c1")).toBeNull();
    expect(getPromptQueue("c2")).toHaveLength(1);

    // Only an explicit resend puts the edited payload back in the queue.
    enqueuePrompt("c1", { ...edited, text: "revised" });
    expect(takeNextPromptForDrain("c1")?.text).toBe("revised");
  });

  it("does not restore a stale item that has already started draining", () => {
    enqueuePrompt("c1", q("running now", "1"));
    takeNextPromptForDrain("c1");
    expect(takePromptForEdit("c1", "1")).toBeNull();
    expect(isPromptDrainLocked("c1")).toBe(true);
  });

  it("drains one at a time per chat", () => {
    enqueuePrompt("c1", q("a", "1"));
    enqueuePrompt("c1", q("b", "2"));
    enqueuePrompt("c2", q("x", "9"));
    expect(takeNextPrompt("c1")?.text).toBe("a");
    expect(getPromptQueue("c1").map((p) => p.text)).toEqual(["b"]);
    expect(getPromptQueue("c2").map((p) => p.text)).toEqual(["x"]);
    expect(takeNextPrompt("c1")?.text).toBe("b");
    expect(takeNextPrompt("c1")).toBeNull();
  });

  it("drain lock blocks a second take until released", () => {
    enqueuePrompt("c1", q("a", "1"));
    enqueuePrompt("c1", q("b", "2"));
    expect(takeNextPromptForDrain("c1")?.text).toBe("a");
    expect(isPromptDrainLocked("c1")).toBe(true);
    expect(takeNextPromptForDrain("c1")).toBeNull();
    releasePromptDrainLock("c1");
    expect(isPromptDrainLocked("c1")).toBe(false);
    expect(takeNextPromptForDrain("c1")?.text).toBe("b");
  });
});
