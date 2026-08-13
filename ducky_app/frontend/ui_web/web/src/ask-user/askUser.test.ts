import { afterEach, describe, expect, it } from "vitest";
import {
  canSubmitQuestion,
  draftToAnswer,
  emptyDraft,
  parseAskUserQuestions,
} from "./types";
import {
  _resetAskUserForTests,
  countAskUserSessionsForConv,
  getAskUserSession,
  getAskUserSessionForConv,
  listAskUserSessions,
  runAskUser,
  settleAskUser,
} from "./runAskUser";
import { _resetFocusedChatForAsk, setFocusedChatForAsk } from "./focusedChatForAsk";

afterEach(() => {
  _resetAskUserForTests();
  _resetFocusedChatForAsk();
});

describe("ask-user types", () => {
  it("parses questions and defaults", () => {
    const qs = parseAskUserQuestions([
      {
        id: "a",
        prompt: "Pick one",
        options: [{ id: "x", label: "X", description: "desc" }],
      },
    ]);
    expect(qs).toHaveLength(1);
    expect(qs[0].allow_free_text).toBe(true);
    expect(qs[0].required).toBe(true);
    expect(qs[0].options[0].description).toBe("desc");
  });

  it("requires selection or other text when required", () => {
    const q = parseAskUserQuestions([
      {
        id: "a",
        prompt: "Pick",
        options: [{ id: "x", label: "X" }],
      },
    ])[0];
    expect(canSubmitQuestion(q, emptyDraft())).toBe(false);
    expect(canSubmitQuestion(q, { selected: ["x"], text: "", other: false })).toBe(true);
    expect(canSubmitQuestion(q, { selected: [], text: "", other: true })).toBe(false);
    expect(canSubmitQuestion(q, { selected: [], text: "custom", other: true })).toBe(true);
  });

  it("maps drafts to answers", () => {
    expect(draftToAnswer({ selected: ["x"], text: "", other: false })).toEqual({
      selected: ["x"],
      text: "",
      skipped: false,
    });
    expect(draftToAnswer({ selected: [], text: "hi", other: true })).toEqual({
      selected: [],
      text: "hi",
      skipped: false,
    });
    expect(draftToAnswer(emptyDraft(), true).skipped).toBe(true);
  });
});

describe("ask-user sessions", () => {
  it("runs concurrent asks in different chats", async () => {
    const a = runAskUser([{ id: "1", prompt: "One" }], "A", "chat-a");
    const b = runAskUser([{ id: "2", prompt: "Two" }], "B", "chat-b");
    expect(getAskUserSessionForConv("chat-a")?.title).toBe("A");
    expect(getAskUserSessionForConv("chat-b")?.title).toBe("B");
    expect(listAskUserSessions()).toHaveLength(2);
    expect(getAskUserSession()).toBeNull();

    const sessA = getAskUserSessionForConv("chat-a")!;
    settleAskUser(
      {
        ok: true,
        answers: { "1": { selected: [], text: "ok", skipped: false } },
        skipped_all: false,
      },
      sessA.id,
    );
    await expect(a).resolves.toMatchObject({ ok: true });
    expect(getAskUserSessionForConv("chat-a")).toBeNull();
    expect(getAskUserSessionForConv("chat-b")?.title).toBe("B");

    const sessB = getAskUserSessionForConv("chat-b")!;
    settleAskUser(
      {
        ok: true,
        answers: { "2": { selected: [], text: "", skipped: true } },
        skipped_all: true,
      },
      sessB.id,
    );
    await expect(b).resolves.toMatchObject({ skipped_all: true });
    expect(listAskUserSessions()).toHaveLength(0);
  });

  it("queues orphan asks (no conv) one at a time", async () => {
    const first = runAskUser([{ id: "1", prompt: "One" }], "A");
    const second = runAskUser([{ id: "2", prompt: "Two" }], "B");
    expect(getAskUserSession()?.title).toBe("A");
    expect(getAskUserSession()?.queueAhead).toBe(1);

    settleAskUser({
      ok: true,
      answers: { "1": { selected: [], text: "ok", skipped: false } },
      skipped_all: false,
    });
    await expect(first).resolves.toMatchObject({ ok: true });
    expect(getAskUserSession()?.title).toBe("B");

    settleAskUser({
      ok: true,
      answers: { "2": { selected: [], text: "", skipped: true } },
      skipped_all: true,
    });
    await expect(second).resolves.toMatchObject({ skipped_all: true });
    expect(getAskUserSession()).toBeNull();
  });

  it("binds empty conv_id to focused chat instead of modal", async () => {
    setFocusedChatForAsk("focused-chat");
    const p = runAskUser([{ id: "1", prompt: "Pick" }], "Title", "");
    expect(getAskUserSession()).toBeNull();
    expect(getAskUserSessionForConv("focused-chat")?.title).toBe("Title");
    const sess = getAskUserSessionForConv("focused-chat")!;
    settleAskUser(
      {
        ok: true,
        answers: { "1": { selected: ["x"], text: "", skipped: false } },
        skipped_all: false,
      },
      sess.id,
    );
    await expect(p).resolves.toMatchObject({ ok: true });
  });

  it("queues member asks on a shared group hub oldest-first", async () => {
    const authorA = { name: "Rigging Ducky", member_conv_id: "m-a" };
    const authorB = { name: "Material Artist", member_conv_id: "m-b" };
    const a = runAskUser([{ id: "1", prompt: "One" }], "A", "m-a", {
      groupIds: ["group-1"],
      author: authorA,
    });
    const b = runAskUser([{ id: "2", prompt: "Two" }], "B", "m-b", {
      groupIds: ["group-1"],
      author: authorB,
    });
    expect(getAskUserSessionForConv("m-a")?.title).toBe("A");
    expect(getAskUserSessionForConv("m-b")?.title).toBe("B");
    const hub = getAskUserSessionForConv("group-1");
    expect(hub?.title).toBe("A");
    expect(hub?.author?.name).toBe("Rigging Ducky");
    expect(hub?.queueAhead).toBe(1);
    expect(countAskUserSessionsForConv("group-1")).toBe(2);

    settleAskUser(
      {
        ok: true,
        answers: { "1": { selected: [], text: "ok", skipped: false } },
        skipped_all: false,
      },
      hub!.id,
    );
    await expect(a).resolves.toMatchObject({ ok: true });
    const next = getAskUserSessionForConv("group-1");
    expect(next?.title).toBe("B");
    expect(next?.queueAhead).toBe(0);
    expect(next?.author?.name).toBe("Material Artist");

    settleAskUser(
      {
        ok: true,
        answers: { "2": { selected: [], text: "", skipped: true } },
        skipped_all: true,
      },
      next!.id,
    );
    await expect(b).resolves.toMatchObject({ skipped_all: true });
    expect(getAskUserSessionForConv("group-1")).toBeNull();
  });
});
