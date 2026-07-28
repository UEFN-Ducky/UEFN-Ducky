import { describe, expect, it } from "vitest";
import {
  appendStreamRow,
  buildCommittedChatRows,
  coalesceActivityRows,
  groupChatRowsIntoTurns,
  userTurnRowIndices,
  type ChatRow,
} from "./chatMessageGroups";
import type { ChatMessage } from "../types/panel";

describe("coalesceActivityRows", () => {
  const tool = (id: string, name?: string): ChatRow => ({
    kind: "tool",
    id,
    intent: {
      id,
      role: "tool",
      text: "",
      tool: { name: name ?? `search_${id}`, arguments: {}, status: "success" },
    },
    result: { id: `${id}-r`, role: "success", text: "ok" },
  });

  it("wraps a lone tool in a 1-tool accordion", () => {
    const rows = coalesceActivityRows([tool("1")]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ kind: "activity", id: "activity-1" });
    if (rows[0].kind !== "activity") throw new Error("expected activity");
    expect(rows[0].items.map((i) => i.id)).toEqual(["1"]);
  });

  it("groups consecutive search tools into one activity accordion", () => {
    const rows = coalesceActivityRows([tool("1"), tool("2"), tool("3")]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ kind: "activity", id: "activity-1" });
    if (rows[0].kind !== "activity") throw new Error("expected activity");
    expect(rows[0].items.map((i) => i.id)).toEqual(["1", "2", "3"]);
  });

  it("keeps only file writes standalone; reads/bash fold into the accordion", () => {
    const rows = coalesceActivityRows([
      tool("1", "workspace_write_file"),
      tool("2", "Bash"),
      tool("3", "Read"),
      tool("4", "search_assets"),
      tool("5", "Grep"),
    ]);
    // Write stays visible; everything else collapses into one "N tools" group.
    expect(rows.map((r) => r.kind)).toEqual(["tool", "activity"]);
    if (rows[1].kind !== "activity") throw new Error("expected activity");
    expect(rows[1].items.map((i) => i.id)).toEqual(["2", "3", "4", "5"]);
  });

  it("keeps any tool with fileEdit meta outside the accordion", () => {
    const editWithDiff: ChatRow = {
      kind: "tool",
      id: "edit",
      intent: {
        id: "edit",
        role: "tool",
        text: "",
        tool: { name: "mystery_patch", arguments: {}, status: "success" },
      },
      result: {
        id: "edit-r",
        role: "success",
        text: "ok",
        tool: {
          name: "mystery_patch",
          arguments: {},
          status: "success",
          fileEdit: {
            path: "Content/Verse/Demo.verse",
            before: "a\n",
            after: "b\n",
            linesAdded: 1,
            linesRemoved: 1,
            kind: "write",
          },
        },
      },
    };
    const rows = coalesceActivityRows([tool("1"), editWithDiff, tool("2")]);
    expect(rows.map((r) => r.kind)).toEqual(["activity", "tool", "activity"]);
    expect(rows[1]).toMatchObject({ kind: "tool", id: "edit" });
  });

  it("folds thinking-only bubbles into the tool run", () => {
    const rows = coalesceActivityRows([
      {
        kind: "bubble",
        id: "th",
        role: "assistant",
        text: "",
        thinking: "considering mesh",
      },
      tool("1"),
      tool("2"),
    ]);
    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe("activity");
    if (rows[0].kind !== "activity") throw new Error("expected activity");
    expect(rows[0].items.map((i) => i.kind)).toEqual(["thinking", "tool", "tool"]);
  });

  it("breaks the group on assistant text but still accordion-wraps the next tool", () => {
    const rows = coalesceActivityRows([
      tool("1"),
      tool("2"),
      { kind: "bubble", id: "a", role: "assistant", text: "done with that" },
      tool("3"),
    ]);
    expect(rows.map((r) => r.kind)).toEqual(["activity", "bubble", "activity"]);
  });

  it("extends a trailing activity when stream thinking arrives", () => {
    const base = coalesceActivityRows([tool("1"), tool("2")]);
    const withStream = coalesceActivityRows([
      ...base,
      {
        kind: "bubble",
        id: "stream",
        role: "assistant",
        text: "",
        thinking: "next…",
        isStreaming: true,
      },
    ]);
    expect(withStream).toHaveLength(1);
    expect(withStream[0].kind).toBe("activity");
    if (withStream[0].kind !== "activity") throw new Error("expected activity");
    expect(withStream[0].items).toHaveLength(3);
    expect(withStream[0].items[2]).toMatchObject({ kind: "thinking", id: "stream" });
  });
});

describe("groupChatRowsIntoTurns", () => {
  it("wraps user query + following AI rows in one turn", () => {
    const rows: ChatRow[] = [
      { kind: "bubble", id: "1", role: "user", text: "hi" },
      { kind: "tool", id: "2", intent: { id: 2, role: "tool", text: "" }, result: null },
      { kind: "bubble", id: "3", role: "assistant", text: "hello" },
      { kind: "bubble", id: "4", role: "user", text: "again" },
      { kind: "bubble", id: "5", role: "assistant", text: "ok" },
    ];
    const turns = groupChatRowsIntoTurns(rows);
    expect(turns).toHaveLength(2);
    expect(turns[0].id).toBe("turn-1");
    expect(turns[0].query?.id).toBe("1");
    expect(turns[0].responses.map((r) => r.id)).toEqual(["2", "3"]);
    expect(turns[1].query?.id).toBe("4");
    expect(turns[1].responses.map((r) => r.id)).toEqual(["5"]);
  });

  it("keeps leading assistant content in a query-less turn", () => {
    const rows: ChatRow[] = [
      { kind: "bubble", id: "a", role: "assistant", text: "solo" },
      { kind: "bubble", id: "u", role: "user", text: "q" },
    ];
    const turns = groupChatRowsIntoTurns(rows);
    expect(turns).toHaveLength(2);
    expect(turns[0].query).toBeNull();
    expect(turns[0].responses.map((r) => r.id)).toEqual(["a"]);
    expect(turns[1].query?.id).toBe("u");
    expect(turns[1].responses).toEqual([]);
  });
});

describe("userTurnRowIndices", () => {
  it("returns indices of user bubble rows only", () => {
    const rows: ChatRow[] = [
      { kind: "bubble", id: "1", role: "user", text: "hi" },
      { kind: "bubble", id: "2", role: "assistant", text: "hello" },
      { kind: "tool", id: "3", intent: { id: 3, role: "tool", text: "" }, result: null },
      { kind: "bubble", id: "4", role: "user", text: "again" },
    ];
    expect(userTurnRowIndices(rows)).toEqual([0, 3]);
  });

  it("returns empty when no user rows", () => {
    const rows: ChatRow[] = [{ kind: "bubble", id: "1", role: "assistant", text: "solo" }];
    expect(userTurnRowIndices(rows)).toEqual([]);
  });
});

describe("buildCommittedChatRows + appendStreamRow", () => {
  const msgs: ChatMessage[] = [
    { id: 1, role: "user", text: "hi" },
    { id: 2, role: "assistant", text: "hello" },
  ];

  it("groups history without stream text", () => {
    const rows = buildCommittedChatRows(msgs, [], true);
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.id)).toEqual(["1", "2"]);
  });

  it("appends a stream row without regrouping prior ids", () => {
    const committed = buildCommittedChatRows(msgs, [], true);
    const withStream = appendStreamRow(committed, "partial…", true, "thinking");
    expect(withStream).toHaveLength(3);
    expect(withStream[0]).toBe(committed[0]);
    expect(withStream[1]).toBe(committed[1]);
    expect(withStream[2]).toMatchObject({
      kind: "bubble",
      id: "stream",
      text: "partial…",
      thinking: "thinking",
      isStreaming: true,
    });
  });

  it("disambiguates duplicate message ids so row ids stay unique", () => {
    // Optimistic tails around a disconnect can mint the same id twice; duplicate
    // keys crash the virtualized list, so grouping must keep every row id unique.
    const dupes: ChatMessage[] = [
      { id: "opt-0", role: "user", text: "q" },
      { id: "opt-0", role: "assistant", text: "a" },
      { id: "opt-0", role: "assistant", text: "b" },
    ];
    const rows = buildCommittedChatRows(dupes, [], false);
    const ids = rows.map((r) => r.id);
    expect(ids).toEqual(["opt-0", "opt-0#1", "opt-0#2"]);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
