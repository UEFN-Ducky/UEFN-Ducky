import { describe, expect, it } from "vitest";
import type { AgentEvent, ChatMessage } from "../../types/panel";
import {
  chatRunReducer,
  initialRunState,
  isRunActive,
  type RunAction,
  type RunState,
} from "./chatRunReducer";

function run(state: RunState, ...actions: RunAction[]): RunState {
  return actions.reduce(chatRunReducer, state);
}

function ev(event: AgentEvent): RunAction {
  return { type: "agentEvent", event };
}

const lastRole = (s: RunState) => s.messages[s.messages.length - 1]?.role;
const texts = (s: RunState) => s.messages.map((m) => m.text);

describe("chatRunReducer — send / accept / stream lifecycle", () => {
  it("send appends the user row and enters 'sending' with the stream cleared", () => {
    const s = run(initialRunState, { type: "send", text: "hello" });
    expect(s.status).toBe("sending");
    expect(s.runId).toBeNull();
    expect(s.stopped).toBe(false);
    expect(lastRole(s)).toBe("user");
    expect(s.messages.at(-1)?.text).toBe("hello");
    expect(s.stream).toBe("");
    expect(isRunActive(s)).toBe(true);
  });

  it("send carries attachments only when present", () => {
    const withAtt = run(initialRunState, {
      type: "send",
      text: "hi",
      attachments: [{ kind: "file", name: "a.txt", text: "x" }],
    });
    expect(withAtt.messages.at(-1)?.attachments).toHaveLength(1);
    const without = run(initialRunState, { type: "send", text: "hi", attachments: [] });
    expect(without.messages.at(-1)?.attachments).toBeUndefined();
  });

  it("sendAccepted moves to 'running' and records the run id", () => {
    const s = run(initialRunState, { type: "send", text: "hi" }, { type: "sendAccepted", runId: "r1" });
    expect(s.status).toBe("running");
    expect(s.runId).toBe("r1");
  });

  it("stopOptimistic (send rejected, no run id) returns to idle", () => {
    const s = run(initialRunState, { type: "send", text: "hi" }, { type: "stopOptimistic" });
    expect(s.status).toBe("idle");
    expect(isRunActive(s)).toBe(false);
  });

  it("text_delta accumulates the answer stream and stays running", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "text_delta", text: "Hel" }),
      ev({ type: "text_delta", text: "lo" }),
    );
    expect(s.status).toBe("running");
    expect(s.stream).toBe("Hello");
  });

  it("thinking accumulates separately from the answer", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      ev({ type: "thinking", text: "reason " }),
      ev({ type: "thinking", text: "more" }),
    );
    expect(s.thinking).toBe("reason more");
    expect(s.stream).toBe("");
  });
});

describe("chatRunReducer — tool interleaving (Cursor-style flush)", () => {
  it("a tool flushes streamed thinking+answer into committed rows above the tool row", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "thinking", text: "let me look" }),
      ev({ type: "text_delta", text: "checking" }),
      ev({ type: "tool", text: "read_file", tool: { name: "workspace_read_file", arguments: {} } }),
    );
    // user, flushed-thinking(assistant), flushed-answer(assistant), tool
    const roles = s.messages.map((m) => m.role);
    expect(roles).toEqual(["user", "assistant", "assistant", "tool"]);
    expect(s.messages[1].thinking).toBe("let me look");
    expect(s.messages[2].text).toBe("checking");
    expect(s.stream).toBe("");
    expect(s.thinking).toBe("");
  });

  it("tool with nothing buffered appends only the tool row", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      ev({ type: "tool", text: "t", tool: { name: "ping", arguments: {} } }),
    );
    expect(s.messages.map((m) => m.role)).toEqual(["user", "tool"]);
  });

  it("tool_done appends a success or error result row", () => {
    const ok = run(
      initialRunState,
      { type: "send", text: "hi" },
      ev({ type: "tool", text: "t", tool: { name: "ping", arguments: {} } }),
      ev({ type: "tool_done", success: true, text: "done", tool: { name: "ping", arguments: {} } }),
    );
    expect(lastRole(ok)).toBe("success");
    const bad = run(
      initialRunState,
      { type: "send", text: "hi" },
      ev({ type: "tool_done", success: false, text: "boom" }),
    );
    expect(lastRole(bad)).toBe("error");
  });

  it("mints unique ids across a multi-row tool flush", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      ev({ type: "thinking", text: "a" }),
      ev({ type: "text_delta", text: "b" }),
      ev({ type: "tool", text: "t", tool: { name: "ping", arguments: {} } }),
    );
    const ids = s.messages.map((m) => String(m.id));
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("chatRunReducer — turn completion", () => {
  it("assistant_done commits the buffered answer+thinking into one bubble", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "thinking", text: "hmm" }),
      ev({ type: "text_delta", text: "Answer" }),
      ev({ type: "assistant_done" }),
    );
    const last = s.messages.at(-1)!;
    expect(last.role).toBe("assistant");
    expect(last.text).toBe("Answer");
    expect(last.thinking).toBe("hmm");
    expect(s.stream).toBe("");
    expect(s.thinking).toBe("");
  });

  it("assistant_done with nothing buffered commits no row", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      ev({ type: "assistant_done" }),
    );
    expect(s.messages.map((m) => m.role)).toEqual(["user"]);
  });

  it("assistant_done commits event.text when stream was empty", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "assistant_done", text: "Artist here!", author: { name: "Artist" } }),
    );
    const last = s.messages.at(-1)!;
    expect(last.role).toBe("assistant");
    expect(last.text).toBe("Artist here!");
    expect(last.author?.name).toBe("Artist");
  });

  it("delegation_warning commits a visible error-styled row", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "do it" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "delegation_warning", text: "No specialist was started." }),
    );
    expect(s.messages.map((m) => m.role)).toEqual(["user", "error"]);
    expect(s.messages[1].text).toContain("No specialist was started.");
  });

  it("agent_stopped(done) at bottom clears the run and requests a reload", () => {
    const before = run(initialRunState, { type: "send", text: "hi" }, { type: "sendAccepted", runId: "r1" });
    const s = run(before, ev({ type: "agent_stopped", reason: "done" }));
    expect(s.status).toBe("idle");
    expect(s.runId).toBeNull();
    expect(s.reloadToken).toBe(before.reloadToken + 1);
  });

  it("agent_stopped(done) when scrolled up flags new-below instead of reloading", () => {
    const before = run(
      initialRunState,
      { type: "atBottom", atBottom: false },
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
    );
    const s = run(before, ev({ type: "agent_stopped", reason: "done" }));
    expect(s.status).toBe("idle");
    expect(s.hasNewBelow).toBe(true);
    expect(s.reloadToken).toBe(before.reloadToken);
  });

  it("agent_stopped(cancelled) commits partial stream and does not reload", () => {
    const before = run(
      initialRunState,
      { type: "atBottom", atBottom: false },
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "thinking", text: "planning folders" }),
    );
    const s = run(before, ev({ type: "agent_stopped", reason: "cancelled" }));
    expect(s.reloadToken).toBe(before.reloadToken);
    expect(s.messages.at(-1)?.role).toBe("assistant");
    expect(s.messages.at(-1)?.thinking).toBe("planning folders");
    expect(s.messages.at(-1)?.incomplete).toBe(true);
  });
});

describe("chatRunReducer — errors", () => {
  it("a crash folds the partial answer into an interrupted assistant bubble", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "text_delta", text: "half" }),
      ev({ type: "error", text: "network blew up" }),
    );
    const last = s.messages.at(-1)!;
    expect(last.role).toBe("assistant");
    expect(last.text).toBe("half");
    expect(last.incomplete).toBe(true);
    expect(last.error).toBe("network blew up");
    expect(s.status).toBe("idle");
  });

  it("a crash with nothing streamed appends a standalone error row", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "error", text: "boom" }),
    );
    expect(lastRole(s)).toBe("error");
    expect(s.status).toBe("idle");
  });

  it("error 'Cancelled' keeps partial stream/thinking and does not reload", () => {
    const before = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "thinking", text: "hmm" }),
      ev({ type: "text_delta", text: "partial" }),
    );
    const s = run(before, ev({ type: "error", text: "Cancelled" }));
    expect(s.status).toBe("idle");
    expect(s.stopped).toBe(true);
    expect(s.reloadToken).toBe(before.reloadToken);
    const last = s.messages.at(-1)!;
    expect(last.role).toBe("assistant");
    expect(last.text).toBe("partial");
    expect(last.thinking).toBe("hmm");
    expect(last.incomplete).toBe(true);
  });
});

describe("chatRunReducer — stop latch & run identity", () => {
  it("invalidate latches 'stopped' so later events are ignored", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      { type: "invalidate" },
      ev({ type: "text_delta", text: "late" }),
      ev({ type: "assistant_done" }),
    );
    expect(s.stream).toBe("");
    expect(s.messages.map((m) => m.role)).toEqual(["user"]);
  });

  it("a user stop beats a still-live global running-set (wedged backend thread)", () => {
    // Repro: backend thread is stuck in a blocking tool call, so it stays in the
    // global running-set and externalRunning never drops. Before the fix, isRunActive
    // OR'd in externalRunning and the Stop button never cleared no matter how many
    // times it was pressed. The stop latch must win.
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      { type: "externalRunning", running: true },
      { type: "invalidate" }, // user presses Stop
      { type: "stopOptimistic" },
    );
    expect(s.stopped).toBe(true);
    expect(s.externalRunning).toBe(true); // backend still churning
    expect(isRunActive(s)).toBe(false); // ...but the UI honors the stop
  });

  it("the next send releases the stop latch", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "one" },
      { type: "invalidate" },
      { type: "send", text: "two" },
      ev({ type: "text_delta", text: "ok" }),
    );
    expect(s.stopped).toBe(false);
    expect(s.stream).toBe("ok");
  });

  it("events for a different run id are dropped once we know our id", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "text_delta", text: "mine", run_id: "r1" }),
      ev({ type: "text_delta", text: "theirs", run_id: "r2" }),
    );
    expect(s.stream).toBe("mine");
  });

  it("events with no run id are accepted before we learn ours", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      ev({ type: "text_delta", text: "early" }),
    );
    expect(s.stream).toBe("early");
  });

  it("stop commits partial stream/thinking and latches stopped", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "thinking", text: "working" }),
      ev({ type: "text_delta", text: "half" }),
      { type: "stop" },
    );
    expect(s.status).toBe("idle");
    expect(s.stopped).toBe(true);
    expect(s.stream).toBe("");
    const last = s.messages.at(-1)!;
    expect(last.role).toBe("assistant");
    expect(last.text).toBe("half");
    expect(last.thinking).toBe("working");
    expect(last.incomplete).toBe(true);
    expect(isRunActive(s)).toBe(false);
  });

  it("stop closes an in-flight tool with a cancelled result so its card stops spinning", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "tool", text: "", tool: { name: "run_tool", arguments: { a: 1 }, status: "pending" } }),
      { type: "stop" },
    );
    const last = s.messages.at(-1)!;
    expect(last.role).toBe("error");
    expect(last.tool?.status).toBe("cancelled");
    expect(last.tool?.name).toBe("run_tool");
    expect(last.tool?.arguments).toEqual({ a: 1 });
  });

  it("stop with no tool in flight mints no synthetic cancelled row", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "text_delta", text: "answer" }),
      { type: "stop" },
    );
    expect(s.messages.some((m) => m.tool?.status === "cancelled")).toBe(false);
  });

  it("a backend 'Cancelled' error also closes an in-flight tool", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "tool", text: "", tool: { name: "run_tool", arguments: {}, status: "pending" } }),
      ev({ type: "error", text: "Cancelled" }),
    );
    expect(s.messages.at(-1)?.tool?.status).toBe("cancelled");
    expect(s.stopped).toBe(true);
  });

  it("agent_stopped(cancelled) closes an in-flight tool", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "tool", text: "", tool: { name: "run_tool", arguments: {}, status: "pending" } }),
      ev({ type: "agent_stopped", reason: "cancelled" }),
    );
    expect(s.messages.at(-1)?.tool?.status).toBe("cancelled");
    expect(s.status).toBe("idle");
  });
});

describe("chatRunReducer — reconcile (the send-race guard)", () => {
  it("never clears while status is 'sending', even on repeated confirmations", () => {
    let s = run(initialRunState, { type: "send", text: "hi" });
    for (let i = 0; i < 5; i++) s = chatRunReducer(s, { type: "reconcile", running: false });
    expect(s.status).toBe("sending");
    expect(isRunActive(s)).toBe(true);
  });

  it("a confirmed 'not running' clears a running turn and requests a reload", () => {
    const before = run(initialRunState, { type: "send", text: "hi" }, { type: "sendAccepted", runId: "r1" });
    const s = chatRunReducer(before, { type: "reconcile", running: false });
    expect(s.status).toBe("idle");
    expect(s.runId).toBeNull();
    expect(s.reloadToken).toBe(before.reloadToken + 1);
  });

  it("a 'still running' confirmation leaves the run untouched", () => {
    const before = run(initialRunState, { type: "send", text: "hi" }, { type: "sendAccepted", runId: "r1" });
    const s = chatRunReducer(before, { type: "reconcile", running: true });
    expect(s).toBe(before);
  });

  it("is a no-op when there is no running turn to reconcile", () => {
    const s = chatRunReducer(initialRunState, { type: "reconcile", running: false });
    expect(s).toBe(initialRunState);
  });
});

describe("chatRunReducer — loaded merge vs replace", () => {
  const backend: ChatMessage[] = [
    { id: 1, role: "user", text: "hi" },
    { id: 2, role: "assistant", text: "hey" },
  ];

  it("replaces the whole tail when idle", () => {
    const s = chatRunReducer(initialRunState, { type: "loaded", rows: backend });
    expect(texts(s)).toEqual(["hi", "hey"]);
    expect(s.stream).toBe("");
    expect(s.hasNewBelow).toBe(false);
  });

  it("keeps the optimistic tail and folds in only missing rows while in flight", () => {
    const inflight = run(initialRunState, { type: "send", text: "new question" });
    const s = chatRunReducer(inflight, { type: "loaded", rows: backend });
    // optimistic user row survives; backend rows it didn't have are prepended.
    expect(s.messages.some((m) => m.text === "new question")).toBe(true);
    expect(s.messages.some((m) => m.text === "hey")).toBe(true);
  });

  it("treats the global running-set as in-flight for merge purposes", () => {
    const s = run(
      initialRunState,
      { type: "externalRunning", running: true },
      { type: "loaded", rows: backend },
    );
    // externalRunning => inFlight => merge (prepend), not replace-and-clear.
    expect(s.messages).toHaveLength(2);
    expect(isRunActive(s)).toBe(true);
  });

  it("merges instead of replacing after a user stop", () => {
    const stopped = run(
      initialRunState,
      { type: "send", text: "hi" },
      ev({ type: "tool", text: "read", tool: { name: "read", arguments: {} } }),
      { type: "stop" },
    );
    const s = chatRunReducer(stopped, { type: "loaded", rows: backend });
    expect(s.messages.some((m) => m.role === "tool")).toBe(true);
  });

  it("does not add a second user bubble when a mid-run reload carries the just-sent message", () => {
    // send() mints an optimistic user row (opt-N); the backend then persists the
    // same message under a numeric id and a context_changed fires a reload while
    // still in flight. The canonical twin must NOT be folded in as a duplicate.
    const inflight = run(initialRunState, { type: "send", text: "new question" });
    const canonical: ChatMessage[] = [
      { id: 1, role: "user", text: "hi" },
      { id: 2, role: "assistant", text: "hey" },
      { id: 3, role: "user", text: "new question" },
    ];
    const s = chatRunReducer(inflight, { type: "loaded", rows: canonical });
    const questionCount = s.messages.filter((m) => m.text === "new question").length;
    expect(questionCount).toBe(1);
    // older backend history it genuinely lacked is still folded in.
    expect(s.messages.some((m) => m.text === "hey")).toBe(true);
  });

  it("still folds in a genuinely new user turn that differs in text", () => {
    const inflight = run(initialRunState, { type: "send", text: "question B" });
    const canonical: ChatMessage[] = [{ id: 9, role: "user", text: "question A" }];
    const s = chatRunReducer(inflight, { type: "loaded", rows: canonical });
    expect(s.messages.some((m) => m.text === "question A")).toBe(true);
    expect(s.messages.some((m) => m.text === "question B")).toBe(true);
  });
});

describe("chatRunReducer — scroll signal", () => {
  it("scrolling to bottom clears the new-below flag", () => {
    const s = run(
      initialRunState,
      { type: "atBottom", atBottom: false },
      { type: "send", text: "hi" },
      ev({ type: "text_delta", text: "x" }),
    );
    expect(s.hasNewBelow).toBe(true);
    const back = chatRunReducer(s, { type: "atBottom", atBottom: true });
    expect(back.hasNewBelow).toBe(false);
  });
});

describe("chatRunReducer — status / thinking footer", () => {
  it("keeps statusText from status events until content arrives", () => {
    const s = run(
      initialRunState,
      { type: "send", text: "hi" },
      { type: "sendAccepted", runId: "r1" },
      ev({ type: "status", text: "Starting Cursor…" }),
    );
    expect(s.statusText).toBe("Starting Cursor…");
    const withThink = chatRunReducer(s, ev({ type: "thinking", text: "hmm" }));
    expect(withThink.thinking).toBe("hmm");
    expect(withThink.statusText).toBe("");
  });
});
