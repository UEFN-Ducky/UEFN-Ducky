// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ChatMessage } from "../types/panel";

const api = vi.hoisted(() => ({ load_tool_result: vi.fn() }));
vi.mock("../hooks/usePanelApi", async (orig) => ({ ...(await orig<object>()), getApi: () => api }));
const { ToolExecutionCard } = await import("./ToolExecutionCard");
const { ChatCollapseScopeProvider } = await import("../hooks/useChatCollapseState");

const CUT = "x".repeat(8_000);
const WHOLE = `${CUT}${"y".repeat(32_000)}TAIL`;
const intent: ChatMessage = { id: 41, role: "tool", text: "", tool: { name: "workspace_read_file", arguments: {}, status: "pending" } };
const row = (tool: Partial<NonNullable<ChatMessage["tool"]>>): ChatMessage =>
  ({ id: 42, role: "success", text: "", tool: { name: "workspace_read_file", arguments: {}, status: "success", result: CUT, ...tool } });

function openCard(result: ChatMessage, scope: string) {
  const view = render(<ChatCollapseScopeProvider scope={scope}><ToolExecutionCard intent={intent} result={result} convId="chat1" /></ChatCollapseScopeProvider>);
  fireEvent.click(view.container.querySelector(".tool-execution-card-toggle")!);
  return view;
}

beforeEach(() => api.load_tool_result.mockReset());
afterEach(cleanup);

it("a cut result offers the rest, and one click shows all of it", async () => {
  api.load_tool_result.mockResolvedValue({ ok: true, result: WHOLE });
  const { container } = openCard(row({ resultTruncated: true, resultBytes: 40_964 }), "cut");
  expect(container.textContent).not.toContain("TAIL");
  fireEvent.click(screen.getByRole("button", { name: "Show full result (40 KB)" }));
  await waitFor(() => expect(container.textContent).toContain("TAIL"));
  expect(api.load_tool_result).toHaveBeenCalledTimes(1);
  expect(api.load_tool_result).toHaveBeenCalledWith("chat1", 42);
  expect(screen.queryByRole("button", { name: /Show full result/ })).toBeNull();
});

it("says so in the card when the rest can't be read", async () => {
  api.load_tool_result.mockResolvedValue({ ok: false, error: "Chat not found" });
  openCard(row({ resultTruncated: true, resultBytes: 40_964 }), "fail");
  fireEvent.click(screen.getByRole("button", { name: /Show full result/ }));
  expect((await screen.findByRole("alert")).textContent).toContain("Chat not found");
  expect(screen.getByRole("button", { name: /Show full result/ })).toBeTruthy();
});

it("a whole result has no button", () => {
  openCard(row({}), "whole");
  expect(screen.queryByRole("button", { name: /Show full result/ })).toBeNull();
});
