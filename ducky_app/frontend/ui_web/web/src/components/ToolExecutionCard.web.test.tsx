// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { ChatMessage } from "../types/panel";
import { ToolExecutionCard } from "./ToolExecutionCard";

afterEach(cleanup);

function card(status: "pending" | "success" | "error" | "cancelled", result = "", args: Record<string, unknown> = {}) {
  const intent = { id: 1, role: "tool", text: "", tool: { name: "web_search", arguments: args, status: "pending" } } as ChatMessage;
  const done = { id: 2, role: status === "success" ? "success" : "error", text: "", tool: { name: "web_search", arguments: args, status, result } } as ChatMessage;
  return render(<ToolExecutionCard intent={intent} result={status === "pending" ? null : done} externalAgent />);
}

it("renders query-only native completion as unavailable details, not an empty search", () => {
  const { container } = card("success", "Codex MCP", { query: "Codex MCP" });
  expect(container.textContent).toContain("Result details unavailable.");
  expect(container.textContent).not.toContain("No results.");
  expect(container.querySelector(".tool-execution-card-toggle-name")?.textContent).toBe("Web lookup");
});

it("keeps pending, failure and cancellation distinct from empty results", () => {
  const pending = card("pending");
  expect(pending.container.textContent).toContain("running");
  expect(pending.container.textContent).not.toContain("No results.");
  pending.unmount();
  const failure = card("error", "Page unavailable");
  expect(failure.container.textContent).toContain("Page unavailable");
  expect(failure.container.textContent).not.toContain("No results.");
  failure.unmount();
  const cancelled = card("cancelled");
  expect(cancelled.container.textContent).toContain("canceled");
  expect(cancelled.container.textContent).not.toContain("No results.");
  expect(cancelled.container.textContent).not.toContain("Result details unavailable.");
});

it("renders a real empty search without suppressing its success status", () => {
  const { container } = card("success", '{"results":[]}');
  expect(container.textContent).toContain("No results.");
  expect(container.querySelector(".tool-execution-card-shell--success")).not.toBeNull();
});

it.each([
  ["search", "Search web"], ["open_page", "Open page"], ["find_in_page", "Find in page"],
])("renders normalized %s completion with available sources and content", (type, label) => {
  const url = "https://developers.openai.com/codex/mcp";
  const { container } = card("success", JSON.stringify({ sources: [{ title: "Codex MCP", url }], text: "MCP configuration reference" }),
    { action: { type, url, query: "Codex MCP", pattern: "MCP" } });
  expect(container.querySelector(".tool-execution-card-toggle-name")?.textContent).toBe(label);
  expect(container.textContent).toContain("MCP configuration reference");
  expect(container.querySelector('a[href="https://developers.openai.com/codex/mcp"]')).not.toBeNull();
  expect(container.textContent).not.toContain("No results.");
  if (type === "find_in_page") expect(container.textContent).toContain("Find in page: MCP");
});

it("does not call missing find details an empty search or activate an unsafe URL", () => {
  const { container } = card("success", "{}", { action: { type: "find_in_page", url: "javascript:alert(1)", pattern: "MCP" } });
  expect(container.textContent).toContain("Result details unavailable.");
  expect(container.textContent).not.toContain("No results.");
  expect(container.querySelector("a")).toBeNull();
});

it("shows search queries when the native action supplies a query list", () => {
  const { container } = card("success", "{}", { action: { type: "search", queries: ["Codex MCP", "MCP configuration"] } });
  expect(container.textContent).toContain("Codex MCP · MCP configuration");
  expect(container.textContent).toContain("Result details unavailable.");
});
