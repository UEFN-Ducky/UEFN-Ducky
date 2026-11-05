// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { FinishedTerminalTail, terminalRunOutput } from "./TerminalTail";

afterEach(() => cleanup());

const run = { ok: true, exit_code: 0, output_tail: "__DUCKY_BEGIN_ab12__\n12 passed in 64.52s\n__DUCKY_DONE_ab12__0__" };

it("reads a finished run's output from its result: object, JSON text or MCP content blocks", () => {
  expect(terminalRunOutput(run)).toContain("12 passed");
  expect(terminalRunOutput(JSON.stringify(run))).toContain("12 passed");
  expect(terminalRunOutput([{ type: "text", text: JSON.stringify(run) }])).toContain("12 passed");
  expect(terminalRunOutput({ ok: true, status: "running" })).toBe("");
  expect(terminalRunOutput("not json")).toBe("");
});

it("a finished test run keeps its output on the card, open, without Ducky's markers", () => {
  render(<FinishedTerminalTail output={run.output_tail} sessionId="s1" />);
  const pre = screen.getByLabelText("Terminal output");
  expect(pre.textContent).toBe("12 passed in 64.52s");
  expect(screen.getByText("Output")).toBeTruthy();
  expect(screen.getByText("Open terminal")).toBeTruthy();
});
