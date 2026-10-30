// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

const openChat = vi.fn<(id: string) => boolean>();
const focusGraph = vi.fn();
const openWorkflows = vi.fn();
vi.mock("../navigation/openChatReference", () => ({ requestOpenChatTab: (id: string) => openChat(id) }));
vi.mock("../hooks/graphActivity", () => ({ requestFocusGraph: (id: string) => focusGraph(id) }));
vi.mock("../navigation/openWorkflowsTab", () => ({ requestOpenWorkflowsTab: () => openWorkflows() }));

import { installOpenFromParent, parseOpenTarget, UD_CURRENT_TARGET, UD_OPEN_TARGET, UD_OPEN_TARGET_ACK } from "./openFromParent";
import { _resetFocusedChatForAsk, setFocusedChatForAsk } from "../ask-user/focusedChatForAsk";

describe("parseOpenTarget", () => {
  it("takes chats and workflows with a safe id", () => {
    expect(parseOpenTarget({ type: UD_OPEN_TARGET, kind: "chat", id: "c_123-abc" })).toEqual({ kind: "chat", id: "c_123-abc" });
    expect(parseOpenTarget({ type: UD_OPEN_TARGET, kind: "workflow", id: "wf.1" })).toEqual({ kind: "workflow", id: "wf.1" });
  });

  it("drops other messages, kinds and ids", () => {
    expect(parseOpenTarget({ type: "ud-pop", kind: "chat", id: "a" })).toBeNull();
    expect(parseOpenTarget({ type: UD_OPEN_TARGET, kind: "settings", id: "a" })).toBeNull();
    expect(parseOpenTarget({ type: UD_OPEN_TARGET, kind: "chat", id: "a/b" })).toBeNull();
    expect(parseOpenTarget({ type: UD_OPEN_TARGET, kind: "chat", id: "" })).toBeNull();
    expect(parseOpenTarget(null)).toBeNull();
  });
});

describe("installOpenFromParent", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    openChat.mockReset();
    focusGraph.mockReset();
    openWorkflows.mockReset();
  });

  function framed() {
    const postMessage = vi.fn();
    const parent = { postMessage } as unknown as Window;
    vi.spyOn(window, "parent", "get").mockReturnValue(parent);
    return { parent, postMessage };
  }

  function send(source: Window, origin: string, data: unknown) {
    window.dispatchEvent(new MessageEvent("message", { data, origin, source: source as MessageEventSource }));
  }

  it("acks the phone and opens the chat once chats are ready", () => {
    vi.useFakeTimers();
    const { parent, postMessage } = framed();
    openChat.mockReturnValueOnce(false).mockReturnValueOnce(false).mockReturnValue(true);
    const stop = installOpenFromParent();
    send(parent, "https://uefnducky.org", { type: UD_OPEN_TARGET, kind: "chat", id: "c1" });
    expect(postMessage).toHaveBeenCalledWith({ type: UD_OPEN_TARGET_ACK, kind: "chat", id: "c1" }, "https://uefnducky.org");
    vi.advanceTimersByTime(250);
    vi.advanceTimersByTime(250);
    expect(openChat).toHaveBeenCalledTimes(3);
    vi.advanceTimersByTime(2000);
    expect(openChat).toHaveBeenCalledTimes(3);
    stop();
  });

  it("opens the Workflows tab on that graph", () => {
    const { parent } = framed();
    const stop = installOpenFromParent();
    send(parent, "https://uefnducky.org", { type: UD_OPEN_TARGET, kind: "workflow", id: "wf1" });
    expect(focusGraph).toHaveBeenCalledWith("wf1");
    expect(openWorkflows).toHaveBeenCalled();
    stop();
  });

  it("tells the phone which chat is on screen, so a reload reopens it", () => {
    const { postMessage } = framed();
    _resetFocusedChatForAsk();
    setFocusedChatForAsk("c_before");
    const stop = installOpenFromParent();
    expect(postMessage).toHaveBeenCalledWith({ type: UD_CURRENT_TARGET, kind: "chat", id: "c_before" }, "https://uefnducky.org");
    setFocusedChatForAsk("c_next");
    expect(postMessage).toHaveBeenLastCalledWith({ type: UD_CURRENT_TARGET, kind: "chat", id: "c_next" }, "https://uefnducky.org");
    stop();
    setFocusedChatForAsk("c_after_stop");
    expect(postMessage).not.toHaveBeenCalledWith(expect.objectContaining({ id: "c_after_stop" }), expect.anything());
    _resetFocusedChatForAsk();
  });

  it("ignores other origins and other senders", () => {
    const { parent, postMessage } = framed();
    const stop = installOpenFromParent();
    send(parent, "https://evil.example", { type: UD_OPEN_TARGET, kind: "chat", id: "c1" });
    send(window, "https://uefnducky.org", { type: UD_OPEN_TARGET, kind: "chat", id: "c1" });
    expect(openChat).not.toHaveBeenCalled();
    expect(postMessage).not.toHaveBeenCalled();
    stop();
  });
});
