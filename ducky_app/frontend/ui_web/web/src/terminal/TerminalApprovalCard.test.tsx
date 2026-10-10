// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { pushLocalAgentEvent } from "../hooks/useAgentEventBus";
import { ChatTerminalApprovals } from "./TerminalApprovalCard";
import { _resetTerminalApprovalsForTests, installTerminalApprovals, listTerminalApprovals } from "./terminalApprovals";

const api = vi.hoisted(() => ({
  terminal_approve_command: vi.fn(),
  terminal_reject_command: vi.fn(),
  terminal_pending_commands: vi.fn(),
}));
vi.mock("../hooks/usePanelApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../hooks/usePanelApi")>()),
  getApi: () => api,
}));

const pending = (request_id: string, conv_id: string, command = "npm test", extra: Record<string, unknown> = {}) => ({
  type: "terminal_command_pending" as const,
  request_id,
  session_id: "s1",
  command,
  shell: "powershell",
  cwd: "C:/repo",
  conv_id,
  rule_label: "npm test",
  local_only: false,
  ...extra,
});

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
}

beforeEach(() => {
  api.terminal_pending_commands.mockResolvedValue([]);
  api.terminal_approve_command.mockResolvedValue({ ok: true });
  api.terminal_reject_command.mockResolvedValue({ ok: true });
  installTerminalApprovals();
});

afterEach(() => {
  cleanup();
  _resetTerminalApprovalsForTests();
  vi.resetAllMocks();
});

describe("terminal command card in the chat that asked", () => {
  it("shows what will run, where, and the four answers, only in its own chat", async () => {
    render(
      <>
        <section aria-label="chat c1"><ChatTerminalApprovals convId="c1" /></section>
        <section aria-label="chat c2"><ChatTerminalApprovals convId="c2" /></section>
      </>,
    );
    act(() => pushLocalAgentEvent(pending("r1", "c1")));
    await flush();
    const c1 = screen.getByLabelText("chat c1");
    expect(c1.textContent).toContain("Allow this terminal command?");
    expect(c1.textContent).toContain("npm test");
    expect(c1.textContent).toContain("Runs in C:/repo · powershell");
    const labels = [...c1.querySelectorAll(".ask-user-option-label")].map((el) => el.textContent);
    expect(labels).toEqual(["Allow once", "Always allow npm test in this chat", "Allow everything in this chat", "Deny"]);
    expect(screen.getByLabelText("chat c2").textContent).toBe("");
    expect(document.querySelector('[data-pending-card="command"]')).toBeTruthy();
  });

  it("Always allow, Allow everything and Deny each call the matching answer", async () => {
    render(<ChatTerminalApprovals convId="c1" />);
    act(() => {
      pushLocalAgentEvent(pending("r1", "c1"));
      pushLocalAgentEvent(pending("r2", "c1", "npm run build"));
      pushLocalAgentEvent(pending("r3", "c1", "git status"));
      pushLocalAgentEvent(pending("r4", "c1", "dir"));
    });
    await flush();
    const card = (id: string) => document.querySelector(`[data-approval-request="${id}"]`) as HTMLElement | null;
    const pick = (id: string, label: string) =>
      [...card(id)!.querySelectorAll("button")].find((b) => b.textContent?.startsWith(label))!;
    fireEvent.click(pick("r1", "Always allow npm test in this chat"));
    await waitFor(() => expect(api.terminal_approve_command).toHaveBeenCalledWith("r1", "always"));
    await waitFor(() => expect(card("r1")).toBeNull());

    fireEvent.click(pick("r2", "Allow everything in this chat"));
    await waitFor(() => expect(api.terminal_approve_command).toHaveBeenCalledWith("r2", "all"));

    fireEvent.click(pick("r3", "Allow once"));
    await waitFor(() => expect(api.terminal_approve_command).toHaveBeenCalledWith("r3", "once"));

    fireEvent.click(pick("r4", "Deny"));
    await waitFor(() => expect(api.terminal_reject_command).toHaveBeenCalledWith("r4"));
    await waitFor(() => expect(document.querySelectorAll("[data-approval-request]")).toHaveLength(0));
  });

  it("closes when answered in another window and never opens again for a replayed request", async () => {
    render(<ChatTerminalApprovals convId="c1" />);
    act(() => pushLocalAgentEvent(pending("r1", "c1")));
    await flush();
    expect(screen.getByText("npm test")).toBeTruthy();
    act(() => pushLocalAgentEvent({ type: "terminal_command_decided", request_id: "r1", conv_id: "c1" }));
    await flush();
    expect(screen.queryByText("npm test")).toBeNull();
    act(() => pushLocalAgentEvent(pending("r1", "c1")));
    await flush();
    expect(screen.queryByText("npm test")).toBeNull();
    expect(listTerminalApprovals()).toEqual([]);
    expect(api.terminal_approve_command).not.toHaveBeenCalled();
  });

  it("offers no Always for a command it never remembers, and no Allow everything for a local-only plugin push", async () => {
    render(<ChatTerminalApprovals convId="c1" />);
    act(() => pushLocalAgentEvent(pending("r1", "c1", "git -C uefn-plugin-openai push", { rule_label: "", local_only: true })));
    await flush();
    const labels = [...document.querySelectorAll(".ask-user-option-label")].map((el) => el.textContent);
    expect(labels).toEqual(["Allow once", "Deny"]);
    expect(screen.getByRole("note").textContent).toContain("local-only AI plugin");
  });

  it("keeps the card and says so when Ducky cannot be reached", async () => {
    api.terminal_approve_command.mockResolvedValue(undefined);
    render(<ChatTerminalApprovals convId="c1" />);
    act(() => pushLocalAgentEvent(pending("r1", "c1")));
    await flush();
    fireEvent.click(screen.getByText("Allow once"));
    expect((await screen.findByRole("alert")).textContent).toContain("Could not reach Ducky");
    expect(document.querySelector('[data-approval-request="r1"]')).toBeTruthy();
  });

  it("a window opened later shows the cards still waiting", async () => {
    _resetTerminalApprovalsForTests();
    api.terminal_pending_commands.mockResolvedValue([
      { request_id: "late", session_id: "s1", command: "npm ci", conv_id: "c1", rule_label: "npm ci", local_only: false, created_at: 1 },
    ]);
    render(<ChatTerminalApprovals convId="c1" />);
    await flush();
    expect(screen.getByText("npm ci")).toBeTruthy();
  });
});
