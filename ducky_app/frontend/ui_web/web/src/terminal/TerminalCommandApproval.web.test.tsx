// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { pushLocalAgentEvent } from "../hooks/useAgentEventBus";
import { TerminalCommandApprovalProvider } from "./TerminalCommandApproval";

afterEach(cleanup);

const pending = (request_id: string, command: string) => ({
  type: "terminal_command_pending" as const,
  request_id,
  session_id: "s1",
  command,
  conv_id: "c1",
});

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
}

describe("TerminalCommandApprovalProvider", () => {
  it("drops a pop-up once its question is answered anywhere, and never asks twice", async () => {
    render(
      <TerminalCommandApprovalProvider>
        <div />
      </TerminalCommandApprovalProvider>,
    );
    act(() => {
      pushLocalAgentEvent(pending("r1", "echo one"));
      pushLocalAgentEvent(pending("r1", "echo one"));
      pushLocalAgentEvent(pending("r2", "echo two"));
    });
    await flush();
    expect(screen.getByText("echo one")).toBeTruthy();
    expect(screen.getByText(/2 in queue/)).toBeTruthy();
    act(() => {
      pushLocalAgentEvent({ type: "terminal_command_decided", request_id: "r1", conv_id: "c1" });
    });
    await flush();
    expect(screen.queryByText("echo one")).toBeNull();
    expect(screen.getByText("echo two")).toBeTruthy();
    expect(screen.queryByText(/in queue/)).toBeNull();
  });
});
