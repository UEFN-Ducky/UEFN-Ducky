// @vitest-environment jsdom
import { act, cleanup, render } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "../types/panel";
import {
  appendStreamRow,
  coalesceActivityRows,
  type ChatRow,
} from "../utils/chatMessageGroups";

/**
 * Render-count probes. The row components are replaced with tiny counters so
 * the test measures the list's reconciliation, not markdown/tool-card cost.
 */
const probes = vi.hoisted(() => {
  const counts = new Map<string, number>();
  const bump = (key: string) => counts.set(key, (counts.get(key) ?? 0) + 1);
  return { counts, bump };
});

vi.mock("./MessageBubble", () => ({
  MessageBubble: (p: { text: string; thinking?: string; isStreaming?: boolean }) => {
    probes.bump(`bubble:${p.isStreaming ? "stream" : p.text}`);
    return <div data-bubble>{p.text}</div>;
  },
}));
vi.mock("./EditableUserMessage", () => ({
  EditableUserMessage: (p: { text: string }) => {
    probes.bump(`user:${p.text}`);
    return <div data-user>{p.text}</div>;
  },
}));
vi.mock("./ToolExecutionCard", () => ({
  ToolExecutionCard: (p: { intent: ChatMessage }) => {
    probes.bump(`tool:${p.intent.id}`);
    return <div data-tool />;
  },
}));
vi.mock("./AgentActivityGroup", () => ({
  AgentActivityGroup: (p: { items: Array<{ id: string }> }) => {
    probes.bump(`activity:${p.items[0]?.id}`);
    return <div data-activity>{p.items.length}</div>;
  },
}));
vi.mock("./AgentActivityPanel", () => ({ AgentActivityPanel: () => <div data-activity-panel /> }));
vi.mock("./ChatPlanPopup", () => ({ ChatPlanPopup: () => null }));
vi.mock("../ask-user", () => ({ AskUserForm: () => null, settleAskUser: vi.fn() }));

import {
  chatListWindowRange,
  resetChatListMountCache,
  VirtualChatMessageList,
  type VirtualChatMessageListHandle,
} from "./VirtualChatMessageList";

function toolMsg(id: number, name: string): ChatMessage {
  return { id, role: "tool", text: "", tool: { name, arguments: {}, status: "success" } } as ChatMessage;
}

/** N turns of: user query, then a 2-tool activity accordion, then an assistant answer. */
function buildHistory(turnCount: number): ChatRow[] {
  const rows: ChatRow[] = [];
  let id = 1;
  for (let t = 0; t < turnCount; t++) {
    rows.push({ kind: "bubble", id: String(id++), role: "user", text: `q${t}` });
    const a = toolMsg(id++, "workspace_read_file");
    const b = toolMsg(id++, "workspace_search");
    rows.push({ kind: "tool", id: String(a.id), intent: a, result: null });
    rows.push({ kind: "tool", id: String(b.id), intent: b, result: null });
    rows.push({ kind: "bubble", id: String(id++), role: "assistant", text: `a${t}` });
  }
  return coalesceActivityRows(rows);
}

const noop = () => {};

function lastUserId(rows: ChatRow[]): string | null {
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    if (r.kind === "bubble" && r.role === "user") return r.id;
  }
  return null;
}

function listProps(rows: ChatRow[]) {
  return {
    rows,
    showActivityPanel: false,
    activityHeaderOnly: false,
    isWaitingOnLinked: false,
    waitingLinked: [],
    isAtBottom: true,
    hasNewBelow: false,
    editableRowId: lastUserId(rows),
    composerMode: "agent" as const,
    composerModel: "m",
    composerCodingAgent: "ducky",
    setComposerCodingAgent: noop,
    convId: "conv",
    onResend: noop,
    onAtBottomChange: noop,
    onJumpToLatest: noop,
    onOpenChat: noop,
    onStopLinked: noop,
    allChats: [],
    linkedAgents: [],
    activityLines: [],
    chatPlan: null,
    chatPlanProgress: null,
    planAllDone: false,
  };
}

/** jsdom reports clientHeight 0, so the list mounts every chunk (see chatListWindowRange). */

function snapshotCounts(): Map<string, number> {
  return new Map(probes.counts);
}

/** Keys whose count moved between two snapshots, ignoring the given prefixes. */
function changedCounts(before: Map<string, number>, after: Map<string, number>, ignore: string[]): string[] {
  const changed: string[] = [];
  for (const [key, n] of before) {
    if (ignore.some((p) => key.startsWith(p))) continue;
    if (after.get(key) !== n) changed.push(`${key}: ${n} -> ${after.get(key)}`);
  }
  return changed;
}

describe("VirtualChatMessageList streaming isolation", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    probes.counts.clear();
    resetChatListMountCache();
    // jsdom has no element.scrollTo / ResizeObserver; the list guards both.
    Object.defineProperty(HTMLElement.prototype, "scrollTo", { value: noop, configurable: true });
  });
  afterEach(() => {
    // No vitest globals → Testing Library does not auto-clean between tests.
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("windows a long list around the viewport with overscan", () => {
    const heights = Array(25).fill(1000);
    expect(chatListWindowRange(10500, 800, heights, 2)).toEqual({ start: 8, end: 13 });
    expect(chatListWindowRange(24000, 800, heights, 2)).toEqual({ start: 22, end: 24 });
    expect(chatListWindowRange(0, 0, heights, 2)).toEqual({ start: 0, end: 24 });
  });

  it("mounts every chunk in jsdom (unknown viewport)", () => {
    const history = buildHistory(100);
    render(<VirtualChatMessageList {...listProps(history)} />);
    const scroller = document.querySelector<HTMLElement>(".virtual-chat-message-list-scroller")!;
    expect(scroller.dataset.chatWindow).toBe("0-12");
    expect(document.querySelectorAll(".virtual-chat-chunk").length).toBe(13);
  });

  it("re-renders only the live turn while an answer streams into a 300-turn chat", () => {
    const history = buildHistory(300);
    const stableProps = listProps(history);
    const view = render(<VirtualChatMessageList {...stableProps} />);
    const afterMount = snapshotCounts();
    // Every history row rendered exactly once on mount: user + activity + assistant per turn.
    expect(afterMount.get("user:q0")).toBe(1);
    expect(afterMount.get("bubble:a299")).toBe(1);
    expect(afterMount.size).toBe(300 * 3);

    const frames = 60;
    let text = "";
    const t0 = performance.now();
    for (let i = 0; i < frames; i++) {
      text += "streamed token ";
      const rows = coalesceActivityRows(appendStreamRow(history, text, true));
      act(() => {
        view.rerender(<VirtualChatMessageList {...stableProps} rows={rows} />);
      });
    }
    const perFrameMs = (performance.now() - t0) / frames;
    const afterStream = snapshotCounts();

    // The previous last answer hands its speak button to the live bubble when the
    // stream starts: exactly one re-render, then it is untouched for all 60 frames.
    expect(afterStream.get("bubble:a299")).toBe(2);
    expect(changedCounts(afterMount, afterStream, ["bubble:stream", "bubble:a299"])).toEqual([]);
    expect(afterStream.get("bubble:stream")).toBe(frames);
    console.log(`[chat-list] 300 turns / ${history.length} rows: ${perFrameMs.toFixed(2)} ms per streamed frame (jsdom)`);
  });

  it("streamed thinking that folds into the last accordion re-renders only that accordion", () => {
    const history = buildHistory(120);
    // Live turn: the user asked, a tool ran, and reasoning streams with no answer text yet.
    const liveUser: ChatRow = { kind: "bubble", id: "u-live", role: "user", text: "live-q" };
    const t = toolMsg(9001, "workspace_read_file");
    const liveTool: ChatRow = { kind: "tool", id: "9001", intent: t, result: null };
    const committed = coalesceActivityRows([...history, liveUser, liveTool]);
    const stableProps = listProps(committed);
    const view = render(<VirtualChatMessageList {...stableProps} />);
    const afterMount = snapshotCounts();

    let thinking = "";
    for (let i = 0; i < 30; i++) {
      thinking += "hmm ";
      const rows = coalesceActivityRows(appendStreamRow(committed, "", true, thinking));
      act(() => {
        view.rerender(<VirtualChatMessageList {...stableProps} rows={rows} />);
      });
    }
    const afterStream = snapshotCounts();
    // The live accordion (first item id 9001) grows a thinking item each frame,
    // and nothing else in the chat re-renders, not even the live turn's query.
    expect(afterStream.get("activity:9001")).toBe(1 + 30);
    expect(changedCounts(afterMount, afterStream, ["activity:9001"])).toEqual([]);
  });

  it("moves the editable flag without re-rendering untouched turns when a new user turn lands", () => {
    const history = buildHistory(50);
    const props = listProps(history);
    const view = render(<VirtualChatMessageList {...props} />);
    const afterMount = snapshotCounts();

    const next: ChatRow[] = [...history, { kind: "bubble", id: "u-new", role: "user", text: "new-q" }];
    act(() => {
      view.rerender(<VirtualChatMessageList {...props} rows={next} editableRowId="u-new" />);
    });
    const after = snapshotCounts();
    expect(after.get("user:new-q")).toBe(1);
    // The previously editable query lost the flag (one re-render); everything else untouched.
    expect(after.get("user:q49")).toBe(2);
    expect(changedCounts(afterMount, after, ["user:q49"])).toEqual([]);
  });

  it("exposes scrollToLatest through the handle", () => {
    const history = buildHistory(3);
    const ref = createRef<VirtualChatMessageListHandle>();
    render(<VirtualChatMessageList ref={ref} {...listProps(history)} />);
    expect(typeof ref.current?.scrollToLatest).toBe("function");
    expect(() => ref.current?.scrollToLatest()).not.toThrow();
  });
});
