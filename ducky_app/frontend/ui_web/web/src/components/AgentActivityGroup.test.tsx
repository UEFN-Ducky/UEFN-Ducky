// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../showme/ShowMeService", async (original) => ({
  ...(await original<typeof import("../showme/ShowMeService")>()),
  playShowMe: vi.fn(async () => ({ ok: true, shown: true, missing: false, target: "x" })),
}));

import { playShowMe } from "../showme/ShowMeService";
import type { ChatMessage } from "../types/panel";
import type { ActivityItem } from "../utils/chatMessageGroups";
import { AgentActivityGroup } from "./AgentActivityGroup";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const tool = (id: string, name: string, args: Record<string, unknown>, status = "success"): ActivityItem => {
  const intent = { role: "tool", content: "", tool: { name, arguments: args, status: "pending" } } as unknown as ChatMessage;
  const result = { role: status === "success" ? "success" : "error", content: "", tool: { name, arguments: args, status, result: "{}" } } as unknown as ChatMessage;
  return { kind: "tool", id, intent, result };
};

describe("Show me in a folded thought group", () => {
  it("keeps a Show me button visible without unfolding, also for a coding agent's wrapped call", async () => {
    const items: ActivityItem[] = [
      { kind: "thinking", id: "t1", text: "Looking for audio settings", isStreaming: false } as unknown as ActivityItem,
      tool("a", "ducky_ui_list_targets", { route: "settings" }),
      tool("b", "CallMcpTool", { toolName: "mcp__uefn__ducky_ui_show", args: { target: "settings.tab.audio", navigate: "settings.audio", title: "Audio settings", body: "Mic and speakers." } }),
    ];
    render(<AgentActivityGroup items={items} />);
    expect(screen.getByRole("button", { name: /Thought process/ }).getAttribute("aria-expanded")).toBe("false");
    const button = screen.getByRole("button", { name: "Show me: Audio settings" });
    fireEvent.click(button);
    await waitFor(() => expect(playShowMe).toHaveBeenCalledWith(expect.objectContaining({ target: "settings.tab.audio", navigate: "settings.audio" })));
  });

  it("shows no button for other tools or a Show me still running", () => {
    render(<AgentActivityGroup items={[tool("a", "web_search", { q: "x" }), { ...tool("b", "ducky_ui_show", { target: "a.b", title: "Later" }), result: null } as ActivityItem]} />);
    expect(screen.queryByRole("group", { name: "Show me" })).toBeNull();
  });
});


it("uses terminal intent status even without a separate result and omits tool Stop controls", () => {
  const item = tool("terminal", "Read", {});
  if (item.kind !== "tool") throw new Error("expected tool");
  item.result = null;
  item.intent.tool!.status = "cancelled";
  render(<AgentActivityGroup items={[item]} />);
  expect(screen.queryByText(/Running tools/)).toBeNull();
  expect(screen.queryByRole("button", { name: /Stop/ })).toBeNull();
});
