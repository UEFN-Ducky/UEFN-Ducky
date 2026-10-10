// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { ChatMessage, FileEditData } from "../types/panel";
import { ToolExecutionCard } from "./ToolExecutionCard";
import { ChatCollapseScopeProvider } from "../hooks/useChatCollapseState";

afterEach(cleanup);

const edit: FileEditData = {
  path: "abs:C:/repo/first.txt", before: "before\n", after: "after\n",
  linesAdded: 1, linesRemoved: 1, kind: "write",
};

it.each(["file_change", "command_execution", "Edit"])("shows %s changes as file cards under one tool row", (name) => {
  const intent: ChatMessage = { id: 1, role: "tool", text: "", tool: { name, arguments: {}, status: "pending" } };
  const done: ChatMessage = { id: 2, role: "success", text: "", tool: {
    name, arguments: {}, status: "success", fileEdit: edit,
    fileEdits: [edit, { ...edit, path: "abs:C:/repo/second.txt" }],
  } };
  const { container } = render(<ChatCollapseScopeProvider scope={`edits-${name}`}>
    <ToolExecutionCard intent={intent} result={done} externalAgent />
  </ChatCollapseScopeProvider>);
  expect(container.querySelectorAll(".tool-execution-card-wrap")).toHaveLength(1);
  expect(container.querySelectorAll(".tool-execution-card-shell")).toHaveLength(1);
  expect(container.querySelectorAll(".tool-file-edit-diff")).toHaveLength(2);
  expect(container.textContent).toContain("first.txt");
  expect(container.textContent).toContain("second.txt");
  expect(container.querySelector(".tool-file-edit-diff-stats")?.textContent).toBe("+1-1");
  fireEvent.click(container.querySelector(".tool-file-edit-diff-header-toggle")!);
  expect(container.querySelector(".tool-file-edit-diff-code")?.textContent).toContain("before");
  expect(container.querySelector(".tool-file-edit-diff-code")?.textContent).toContain("after");
});

it("preserves legacy single-file cards", () => {
  const row: ChatMessage = { id: 1, role: "success", text: "", tool: { name: "Edit", arguments: {}, fileEdit: edit } };
  const { container } = render(<ToolExecutionCard intent={{ ...row, role: "tool" }} result={row} />);
  expect(container.querySelectorAll(".tool-file-edit-diff")).toHaveLength(1);
});

it("does not fabricate a card when authoritative snapshots found no changed file", () => {
  const row: ChatMessage = { id: 1, role: "success", text: "", tool: { name: "Edit", arguments: {}, fileEdit: edit, fileEdits: [] } };
  const { container } = render(<ToolExecutionCard intent={{ ...row, role: "tool" }} result={row} />);
  expect(container.querySelectorAll(".tool-file-edit-diff")).toHaveLength(0);
});
