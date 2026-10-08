// @vitest-environment jsdom
import { act, cleanup, fireEvent, render } from "@testing-library/react";
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
    setComposerModel: noop,
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

/** jsdom reports clientHeight 0; applyWindow stays on the tail instead of every chunk. */

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
    // Helper still covers the whole list when height is unknown; applyWindow
    // must not use this path on first paint (see tail-only jsdom test below).
    expect(chatListWindowRange(0, 0, heights, 2)).toEqual({ start: 0, end: 24 });
  });

  it("mounts only the tail in jsdom (unknown viewport)", () => {
    const history = buildHistory(100);
    render(<VirtualChatMessageList {...listProps(history)} />);
    const scroller = document.querySelector<HTMLElement>(".virtual-chat-message-list-scroller")!;
    expect(scroller.dataset.chatWindow).toBe("97-99");
    expect(document.querySelectorAll(".virtual-chat-chunk").length).toBe(3);
  });

  it("also windows a report-heavy chat with only 17 turns", () => {
    const history = buildHistory(17);
    render(<VirtualChatMessageList {...listProps(history)} />);
    expect(document.querySelectorAll(".virtual-chat-turn")).toHaveLength(3);
    expect(probes.counts.has("user:q0")).toBe(false);
    expect(probes.counts.get("bubble:a16")).toBe(1);
  });

  it("re-renders only the live turn while an answer streams into a 300-turn chat", () => {
    const history = buildHistory(300);
    const stableProps = listProps(history);
    const view = render(<VirtualChatMessageList {...stableProps} />);
    const afterMount = snapshotCounts();
    // First paint with no viewport keeps three tail turns, not all 300 turns.
    expect(afterMount.get("user:q0")).toBeUndefined();
    expect(afterMount.get("bubble:a299")).toBe(1);
    expect(afterMount.size).toBe(3 * 3);

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

  it("synchronizes a remounted tail with cached bottom state", () => {
    const onAtBottomChange = vi.fn();
    render(<VirtualChatMessageList {...listProps(buildHistory(3))} isAtBottom={false} onAtBottomChange={onAtBottomChange} />);
    expect(onAtBottomChange).toHaveBeenLastCalledWith(true);
  });

  it("exposes scrollToLatest through the handle", () => {
    const history = buildHistory(3);
    const ref = createRef<VirtualChatMessageListHandle>();
    render(<VirtualChatMessageList ref={ref} {...listProps(history)} />);
    expect(typeof ref.current?.scrollToLatest).toBe("function");
    expect(() => ref.current?.scrollToLatest()).not.toThrow();
  });
});

describe("chat scroll intent during layout changes", () => {
  let observers: Set<{ targets: Set<Element>; callback: ResizeObserverCallback }>;
  beforeEach(() => {
    vi.useFakeTimers();
    observers = new Set();
    vi.stubGlobal("ResizeObserver", class {
      targets = new Set<Element>();
      constructor(public callback: ResizeObserverCallback) { observers.add(this); }
      observe(element: Element) { this.targets.add(element); }
      unobserve(element: Element) { this.targets.delete(element); }
      disconnect() { observers.delete(this); }
    });
    Object.defineProperty(HTMLElement.prototype, "scrollTo", { value: noop, configurable: true });
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  function setup() {
    const ref = createRef<VirtualChatMessageListHandle>();
    const props = { ...listProps(buildHistory(300)), onAtBottomChange: vi.fn() };
    const rendered = render(<VirtualChatMessageList ref={ref} {...props} />);
    const scroller = document.querySelector<HTMLElement>(".virtual-chat-message-list-scroller")!;
    const content = document.querySelector<HTMLElement>(".virtual-chat-message-list-content")!;
    let height = 240024;
    Object.defineProperties(scroller, {
      scrollHeight: { get: () => height, configurable: true },
      clientHeight: { value: 600, configurable: true },
      scrollTop: { value: height - 600, writable: true, configurable: true },
    });
    const scrollTo = vi.fn(({ top }: ScrollToOptions) => { scroller.scrollTop = Math.max(0, Math.min(top || 0, height - 600)); });
    Object.defineProperty(scroller, "scrollTo", { value: scrollTo, configurable: true });
    const resize = (nextHeight = height) => {
      height = nextHeight;
      act(() => { for (const observer of [...observers]) if (observer.targets.has(content)) observer.callback([], observer as unknown as ResizeObserver); });
    };
    resize();
    scrollTo.mockClear();
    props.onAtBottomChange.mockClear();
    const scroll = (top: number) => { scroller.scrollTop = top; fireEvent.scroll(scroller); };
    const frame = () => act(() => { vi.advanceTimersByTime(20); });
    return { ref, rendered, props, scroller, scrollTo, resize, scroll, frame, onAtBottomChange: props.onAtBottomChange };
  }

  it("releases follow on a tiny upward wheel before resize or scroll callbacks run", () => {
    const view = setup();
    fireEvent.wheel(view.scroller, { deltaY: -4 });
    view.resize(241024);
    expect(view.scrollTo).not.toHaveBeenCalled();
    expect(view.onAtBottomChange).toHaveBeenCalledWith(false);
  });
  it("releases an upward scrollbar move synchronously before a resize callback", () => {
    const view = setup();
    view.scroll(238000);
    view.resize(241024);
    expect(view.scrollTo).not.toHaveBeenCalled();
  });
  it("does not mistake content shrinking to the viewport for returning to the bottom", () => {
    const view = setup();
    fireEvent.wheel(view.scroller, { deltaY: -300 });
    view.scroll(238000);
    view.frame();
    view.resize(238600);
    view.scroll(238000);
    view.frame();
    view.scrollTo.mockClear();
    view.resize(245000);
    expect(view.scrollTo).not.toHaveBeenCalled();
    expect(view.scroller.scrollTop).toBe(238000);
  });
  it("cancels a queued jump when the user starts reading older messages", () => {
    const view = setup();
    act(() => view.ref.current?.scrollToLatest());
    fireEvent.wheel(view.scroller, { deltaY: -200 });
    view.scroll(238000);
    view.frame();
    expect(view.scrollTo).not.toHaveBeenCalled();
    expect(view.scroller.scrollTop).toBe(238000);
  });
  it.each(["ArrowUp", "PageUp", "Home"])("releases immediately on %s", (key) => {
    const view = setup();
    fireEvent.keyDown(view.scroller, { key });
    view.resize(241024);
    expect(view.scrollTo).not.toHaveBeenCalled();
  });
  it("releases immediately when swiping toward older messages", () => {
    const view = setup();
    fireEvent.touchStart(view.scroller, { touches: [{ clientY: 100 }] });
    fireEvent.touchMove(view.scroller, { touches: [{ clientY: 140 }] });
    view.resize(241024);
    expect(view.scrollTo).not.toHaveBeenCalled();
  });
  it("resumes after deliberately scrolling down to the bottom", () => {
    const view = setup();
    fireEvent.wheel(view.scroller, { deltaY: -300 });
    view.scroll(238000);
    view.frame();
    fireEvent.wheel(view.scroller, { deltaY: 1500 });
    view.scroll(239424);
    view.frame();
    view.scrollTo.mockClear();
    view.resize(241024);
    expect(view.scrollTo).toHaveBeenCalled();
    expect(view.onAtBottomChange).toHaveBeenLastCalledWith(true);
  });
  it("ignores fractional layout rounding at the bottom", () => {
    const view = setup();
    view.scroll(239423);
    view.frame();
    expect(view.onAtBottomChange).not.toHaveBeenCalledWith(false);
    view.resize(241024);
    expect(view.scrollTo).toHaveBeenCalled();
  });
  it("does not treat arrow keys inside a message editor as chat scrolling", () => {
    const view = setup();
    const editor = document.createElement("textarea");
    view.scroller.appendChild(editor);
    fireEvent.keyDown(editor, { key: "ArrowUp" });
    view.resize(241024);
    expect(view.scrollTo).toHaveBeenCalled();
    editor.remove();
  });
  it("can explicitly jump back and follow, and cancels pending work on unmount", () => {
    const view = setup();
    fireEvent.wheel(view.scroller, { deltaY: -300 });
    view.scroll(238000);
    view.frame();
    act(() => view.ref.current?.scrollToLatest());
    view.frame();
    expect(view.scroller.scrollTop).toBe(239424);
    expect(view.onAtBottomChange).toHaveBeenLastCalledWith(true);
    view.scrollTo.mockClear();
    act(() => view.ref.current?.scrollToLatest());
    view.rendered.unmount();
    view.frame();
    expect(view.scrollTo).not.toHaveBeenCalled();
  });
  it("does not render older messages again when the offscreen answer streams", () => {
    const view = setup();
    fireEvent.wheel(view.scroller, { deltaY: -1000 });
    view.scroll(100000);
    view.frame();
    const before = snapshotCounts();
    expect(document.querySelectorAll(".virtual-chat-chunk").length).toBeLessThanOrEqual(4);
    for (let i = 1; i <= 30; i++) {
      const rows = coalesceActivityRows(appendStreamRow(view.props.rows, "Streamed text ".repeat(i), true));
      view.rendered.rerender(<VirtualChatMessageList ref={view.ref} {...view.props} rows={rows} />);
    }
    expect(changedCounts(before, snapshotCounts(), [])).toEqual([]);
    expect(view.scroller.scrollTop).toBe(100000);
  });
  it("keeps the reader's window through hidden layout and background updates", () => {
    const view = setup();
    fireEvent.wheel(view.scroller, { deltaY: -1000 });
    view.scroll(100000);
    view.frame();
    const windowBefore = view.scroller.dataset.chatWindow;
    const chunksBefore = [...document.querySelectorAll(".virtual-chat-chunk")];
    Object.defineProperty(view.scroller, "clientHeight", { value: 0, configurable: true });
    view.resize();
    fireEvent.scroll(view.scroller);
    const rows = [...view.props.rows, { kind: "bubble" as const, id: "background-user", role: "user" as const, text: "next task" }];
    view.rendered.rerender(<VirtualChatMessageList ref={view.ref} {...view.props} rows={rows} />);
    view.frame();
    expect(view.scroller.dataset.chatWindow).toBe(windowBefore);
    expect([...document.querySelectorAll(".virtual-chat-chunk")]).toEqual(chunksBefore);
    Object.defineProperty(view.scroller, "clientHeight", { value: 600, configurable: true });
    view.resize();
    expect(view.scroller.scrollTop).toBe(100000);
    expect(view.scrollTo).not.toHaveBeenCalled();
    expect(view.scroller.dataset.chatWindow).toBe(windowBefore);
  });

  it("keeps following content growth until the user scrolls away", () => {
    const view = setup();
    view.resize(245000);
    expect(view.scrollTo).toHaveBeenCalledWith({ top: 245000, behavior: "auto" });
  });
});
