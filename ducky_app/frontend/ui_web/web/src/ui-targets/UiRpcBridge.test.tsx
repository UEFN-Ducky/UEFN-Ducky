// @vitest-environment jsdom
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentEvent } from "../types/panel";

let handler: ((event: AgentEvent) => void) | null = null;
vi.mock("../hooks/useAgentEventBus", () => ({
  installAgentEventBus: vi.fn(),
  subscribeAgentEvents: (fn: (event: AgentEvent) => void) => {
    handler = fn;
    return () => { handler = null; };
  },
}));
const api = {
  ui_rpc_respond: vi.fn(async () => true),
  ui_rpc_ack: vi.fn(async () => true),
  ui_rpc_active: vi.fn(async () => true),
  ui_rpc_claim: vi.fn(async () => true),
  ui_rpc_pending_questions: vi.fn(async (): Promise<AgentEvent[]> => []),
  get_settings: vi.fn(async () => ({})),
};
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false }));
vi.mock("../hooks/onApiReady", () => ({
  onApiReady: (fn: (ready: typeof api) => void) => { fn(api); return () => {}; },
}));

import { UI_CLIENT_ID, UiRpcBridge } from "./UiRpcBridge";

const request = (id: string, method: string, params: Record<string, unknown>) =>
  handler?.({ type: "ui_rpc_request", request_id: id, method, params } as unknown as AgentEvent);

beforeEach(() => render(<UiRpcBridge />));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("which window answers", () => {
  it("only the window the request is for takes a navigate / show / tour, and says so", async () => {
    request("r1", "list_targets", { route: "workflows", _for_client: "another-window" });
    await new Promise((r) => setTimeout(r, 30));
    expect(api.ui_rpc_ack).not.toHaveBeenCalled();
    expect(api.ui_rpc_respond).not.toHaveBeenCalled();

    request("r2", "list_targets", { route: "workflows", _for_client: UI_CLIENT_ID });
    await waitFor(() => expect(api.ui_rpc_respond).toHaveBeenCalledWith("r2", expect.objectContaining({ targets: expect.any(Array), actions: expect.any(Array) })));
    expect(api.ui_rpc_ack).toHaveBeenCalledWith("r2");

    request("r3", "list_targets", { route: "workflows" });  // no window named: the first to claim it
    await waitFor(() => expect(api.ui_rpc_respond).toHaveBeenCalledWith("r3", expect.anything()));
    expect(api.ui_rpc_claim).toHaveBeenCalledWith("r3");

    api.ui_rpc_claim.mockResolvedValueOnce(false);  // another window claimed it first
    request("r5", "list_targets", { route: "workflows" });
    await new Promise((r) => setTimeout(r, 30));
    expect(api.ui_rpc_respond).not.toHaveBeenCalledWith("r5", expect.anything());
  });

  it("closes an approval card here when another window or the phone answered it", async () => {
    const { countAskUserSessionsForConv } = await import("../ask-user");
    request("r7", "ask_user", { conv_id: "c1", questions: [{ id: "agent_permission", prompt: "Allow?", options: [{ id: "once", label: "Allow once" }] }] });
    await waitFor(() => expect(countAskUserSessionsForConv("c1")).toBe(1));
    handler?.({ type: "ui_rpc_settled", request_id: "r7" } as unknown as AgentEvent);
    await waitFor(() => expect(countAskUserSessionsForConv("c1")).toBe(0));
    await waitFor(() => expect(api.ui_rpc_respond).toHaveBeenCalledWith("r7", { error: "answered in another window" }));
  });

  it("restores unanswered questions after reload and deduplicates live requests", async () => {
    cleanup();
    const event = {
      type: "ui_rpc_request", request_id: "replayed", method: "ask_user",
      params: { conv_id: "restored", questions: [{ id: "q", prompt: "Continue?" }] },
    } as unknown as AgentEvent;
    api.ui_rpc_pending_questions.mockResolvedValueOnce([event]);
    render(<UiRpcBridge />);
    const { countAskUserSessionsForConv } = await import("../ask-user");
    await waitFor(() => expect(countAskUserSessionsForConv("restored")).toBe(1));
    handler?.(event);
    await waitFor(() => expect(countAskUserSessionsForConv("restored")).toBe(1));
    expect(api.ui_rpc_respond).not.toHaveBeenCalledWith("replayed", expect.anything());
    handler?.({ type: "ui_rpc_settled", request_id: "replayed" } as unknown as AgentEvent);
    await waitFor(() => expect(countAskUserSessionsForConv("restored")).toBe(0));
  });

  it("does not restore a question answered while replay was loading", async () => {
    cleanup();
    let release!: (events: AgentEvent[]) => void;
    api.ui_rpc_pending_questions.mockReturnValueOnce(new Promise((resolve) => { release = resolve; }));
    render(<UiRpcBridge />);
    handler?.({ type: "ui_rpc_settled", request_id: "already-answered" } as unknown as AgentEvent);
    release([{
      type: "ui_rpc_request", request_id: "already-answered", method: "ask_user",
      params: { conv_id: "race", questions: [{ id: "q", prompt: "Continue?" }] },
    } as unknown as AgentEvent]);
    await new Promise((resolve) => setTimeout(resolve, 0));
    const { countAskUserSessionsForConv } = await import("../ask-user");
    expect(countAskUserSessionsForConv("race")).toBe(0);
  });

  it("claims active when the user clicks or types here", () => {
    api.ui_rpc_active.mockClear();
    fireEvent.pointerDown(document.body);
    expect(api.ui_rpc_active).toHaveBeenCalledWith(UI_CLIENT_ID, true);
  });

  it("with Let Ducky show me things off, the AI's Show me only leaves its button", async () => {
    api.get_settings.mockResolvedValueOnce({ show_me_autoplay: false });
    request("r4", "show", { target: "workflows.toolbar.run", title: "Test" });
    await waitFor(() => expect(api.ui_rpc_respond).toHaveBeenCalledWith("r4", expect.objectContaining({ deferred: true, shown: false })));
  });
});
