// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ChatCollapseScopeProvider } from "../hooks/useChatCollapseState";
import type { ChatMessage } from "../types/panel";
import { ToolExecutionCard } from "./ToolExecutionCard";

afterEach(cleanup);

// Open/closed is remembered per chat scope across renders: every card gets its own scope.
let nextId = 100;

function renderCard(intent: ChatMessage, result: ChatMessage | null) {
  nextId += 2;
  return render(
    <ChatCollapseScopeProvider scope={`web-test-${nextId}`}>
      <ToolExecutionCard intent={intent} result={result} externalAgent />
    </ChatCollapseScopeProvider>,
  );
}

function card(status: "pending" | "success" | "error" | "cancelled", result = "", args: Record<string, unknown> = {}, name = "web_search") {
  nextId += 2;
  const intent = { id: nextId, role: "tool", text: "", tool: { name, arguments: args, status: "pending" } } as ChatMessage;
  const done = { id: nextId + 1, role: status === "success" ? "success" : "error", text: "", tool: { name, arguments: args, status, result } } as ChatMessage;
  const view = renderCard(intent, status === "pending" ? null : done);
  // Web rows open like any tool row; the result shows once opened.
  const toggle = view.container.querySelector(".tool-execution-card-toggle");
  if (toggle) fireEvent.click(toggle);
  return view;
}

it("keeps a web row folded to one line with its own shade, icon and what it looked at", () => {
  const intent = { id: 1, role: "tool", text: "", tool: { name: "web_video", arguments: { url: "https://www.youtube.com/watch?v=FBs--2nSPLQ", frames: [13, 62] }, status: "pending" } } as ChatMessage;
  const done = { ...intent, id: 2, role: "success", tool: { ...intent.tool!, status: "success", result: '{"ok":true,"title":"I challenged Rust Developers","excerpt":"Hidden until opened"}' } } as ChatMessage;
  const { container } = renderCard(intent, done);
  expect(container.querySelector(".tool-execution-card-shell--web-video")).not.toBeNull();
  expect(container.querySelector(".tool-execution-card-toggle-detail")?.textContent).toBe("youtube.com · 2 frames");
  expect(container.textContent).not.toContain("Hidden until opened");
  fireEvent.click(container.querySelector(".tool-execution-card-toggle")!);
  expect(container.textContent).toContain("Hidden until opened");
});

it("shows a picture search's pictures without opening it", () => {
  const intent = { id: 1, role: "tool", text: "", tool: { name: "web_search", arguments: { query: "flags", images: true }, status: "pending" } } as ChatMessage;
  const done = { ...intent, id: 2, role: "success", tool: { ...intent.tool!, status: "success", result: JSON.stringify({ ok: true, results: [], images: [{ title: "Flag", url: "https://example.com/f", thumb: "https://example.com/f.jpg" }] }) } } as ChatMessage;
  const { container } = renderCard(intent, done);
  expect(container.querySelector('img[src="https://example.com/f.jpg"]')).not.toBeNull();
})

it("minimizes an open picture search from its header, and opens it again", () => {
  const intent = { id: 1, role: "tool", text: "", tool: { name: "web_search", arguments: { query: "flags", images: true }, status: "pending" } } as ChatMessage;
  const done = { ...intent, id: 2, role: "success", tool: { ...intent.tool!, status: "success", result: JSON.stringify({ ok: true, results: [], images: [{ title: "Flag", url: "https://example.com/f", thumb: "https://example.com/f.jpg" }] }) } } as ChatMessage;
  const { container } = renderCard(intent, done);
  const toggle = container.querySelector(".tool-execution-card-toggle")!;
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  fireEvent.click(toggle);
  expect(container.querySelector("img")).toBeNull();
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(toggle);
  expect(container.querySelector('img[src="https://example.com/f.jpg"]')).not.toBeNull();
});

it.each(["ducky_generate_image", "take_high_res_screenshot"])("shows what %s made without a click, and its header minimizes it", (name) => {
  const url = "http://127.0.0.1:4199/tool-captures/made_1.png";
  const intent = { id: 1, role: "tool", text: "", tool: { name, arguments: {}, status: "pending" } } as ChatMessage;
  const done = { ...intent, id: 2, role: "success", tool: { ...intent.tool!, status: "success", result: JSON.stringify({ ok: true, path: "C:/x/made_1.png", media_url: url }) } } as ChatMessage;
  const { container } = renderCard(intent, done);
  const toggle = container.querySelector(".tool-execution-card-toggle")!;
  expect(container.querySelector(".tool-card-screenshot-img")).not.toBeNull();
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  fireEvent.click(toggle);
  expect(container.querySelector("img")).toBeNull();
  fireEvent.click(toggle);
  expect(container.querySelector(".tool-card-screenshot-img")).not.toBeNull();
});

it("keeps a looked-at picture closed until its row is opened", () => {
  const url = "http://127.0.0.1:4199/chat-attachments/c1/viewed_1.png";
  const intent = { id: 1, role: "tool", text: "", tool: { name: "mcp__uefn__view_image", arguments: {}, status: "pending" } } as ChatMessage;
  const done = { ...intent, id: 2, role: "success", tool: { ...intent.tool!, status: "success", result: JSON.stringify({ path: "C:/x/viewed_1.png", bytes: 9, media_url: url }) } } as ChatMessage;
  const { container } = renderCard(intent, done);
  const toggle = container.querySelector(".tool-execution-card-toggle")!;
  expect(container.querySelector("img")).toBeNull();
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(toggle);
  expect(container.querySelector(".tool-card-screenshot-img")).not.toBeNull();
});

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

it("shows a failed command with its exit code on a red tool row", () => {
  const result: ChatMessage = { id: 2, role: "error", text: "tests failed", tool: { name: "Bash", arguments: { command: "pytest" }, status: "error", exitCode: 1 } };
  const { container } = render(<ToolExecutionCard intent={{ ...result, role: "tool" }} result={result} externalAgent />);
  expect(container.querySelector(".tool-execution-card-shell--error")).toBeTruthy();
  expect(container.textContent).toContain("failed");
  expect(container.textContent).toContain("exit 1");
  expect(container.textContent).not.toContain("Interrupted");
});

it.each(["Exit code: 2\nTwo tests failed", '{"exit_code":2,"output":"failed"}'])("keeps a checkpoint exit code visible from result text: %s", (output) => {
  const result: ChatMessage = { id: 2, role: "error", text: "", tool: { name: "Bash", arguments: {}, status: "error", result: output } };
  const { container } = render(<ToolExecutionCard intent={{ ...result, role: "tool" }} result={result} externalAgent />);
  expect(container.textContent).toContain("exit 2");
  expect(container.textContent).not.toContain("Interrupted");
});
