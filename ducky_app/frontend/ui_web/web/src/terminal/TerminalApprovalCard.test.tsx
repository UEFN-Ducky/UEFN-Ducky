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
  ...extra,
});

// The bus hands events to subscribers once per frame: wait for what a test needs, never a fixed time
// (a slow Windows runner had r1 shown and r2-r4 still queued after 30 ms; they leaked into the next test).
const shown = (id: string) => document.querySelector(`[data-approval-request="${id}"]`) as HTMLElement | null;

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
    await waitFor(() => expect(shown("r1")).toBeTruthy());
    const c1 = screen.getByLabelText("chat c1");
    expect(c1.textContent).toContain("Allow this terminal command?");
    expect(c1.textContent).toContain("npm test");
    expect(c1.textContent).toContain("Runs in C:/repo · powershell");
    const labels = [...c1.querySelectorAll(".ask-user-option-label")].map((el) => el.textContent);
    expect(labels).toEqual(["Allow once", "Always allow npm test in this chat", "Allow everything in this chat", "Deny"]);
    expect(screen.getByLabelText("chat c2").textContent).toBe("");
    expect(document.querySelector('[data-pending-card="command"]')).toBeTruthy();
  });

  it("a team member's card also shows in its lead's chat, named, and answers there for the lead", async () => {
    // Oct 15 2026: a member's command card waited in its own chat, nobody saw it, and the team stopped.
    render(
      <>
        <section aria-label="member"><ChatTerminalApprovals convId="member" /></section>
        <section aria-label="lead"><ChatTerminalApprovals convId="lead" /></section>
        <section aria-label="other"><ChatTerminalApprovals convId="other" /></section>
      </>,
    );
    act(() => pushLocalAgentEvent(pending("r9", "member", "npm test", { lead_conv_id: "lead", asked_by: "Verse Coder" })));
    await waitFor(() => expect(document.querySelectorAll('[data-approval-request="r9"]')).toHaveLength(2));
    expect(screen.getByLabelText("member").textContent).toContain("Allow this terminal command?");
    const lead = screen.getByLabelText("lead");
    expect(lead.textContent).toContain("Verse Coder wants to run a terminal command");
    expect(lead.textContent).toContain("Never ask again in this chat or the agents its plan starts");
    expect(screen.getByLabelText("other").textContent).toBe("");
    const all = [...lead.querySelectorAll("button")].find((b) => b.textContent?.startsWith("Allow everything"))!;
    fireEvent.click(all);
    await waitFor(() => expect(api.terminal_approve_command).toHaveBeenCalledWith("r9", "all", "lead"));
    await waitFor(() => expect(document.querySelectorAll("[data-approval-request]")).toHaveLength(0));
  });

  it("Always allow, Allow everything and Deny each call the matching answer", async () => {
    render(<ChatTerminalApprovals convId="c1" />);
    act(() => {
      pushLocalAgentEvent(pending("r1", "c1"));
      pushLocalAgentEvent(pending("r2", "c1", "npm run build"));
      pushLocalAgentEvent(pending("r3", "c1", "git status"));
      pushLocalAgentEvent(pending("r4", "c1", "dir"));
    });
    await waitFor(() => expect(document.querySelectorAll("[data-approval-request]")).toHaveLength(4));
    const card = shown;
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
    await waitFor(() => expect(screen.getByText("npm test")).toBeTruthy());
    act(() => pushLocalAgentEvent({ type: "terminal_command_decided", request_id: "r1", conv_id: "c1" }));
    await waitFor(() => expect(screen.queryByText("npm test")).toBeNull());
    // The replay, then a marker in another chat: once the marker is in, the replay was handled.
    act(() => {
      pushLocalAgentEvent(pending("r1", "c1"));
      pushLocalAgentEvent(pending("marker", "c9"));
    });
    await waitFor(() => expect(listTerminalApprovals().map((item) => item.request_id)).toEqual(["marker"]));
    expect(screen.queryByText("npm test")).toBeNull();
    expect(api.terminal_approve_command).not.toHaveBeenCalled();
  });

  it("offers no Always for a command it never remembers, and a push in any plugin can still allow everything", async () => {
    render(<ChatTerminalApprovals convId="c1" />);
    act(() => pushLocalAgentEvent(pending("r1", "c1", "git -C uefn-plugin-openai push", { rule_label: "" })));
    await waitFor(() => expect(shown("r1")).toBeTruthy());
    const labels = [...document.querySelectorAll(".ask-user-option-label")].map((el) => el.textContent);
    expect(labels).toEqual(["Allow once", "Allow everything in this chat", "Deny"]);
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("keeps the card and says so when Ducky cannot be reached", async () => {
    api.terminal_approve_command.mockResolvedValue(undefined);
    render(<ChatTerminalApprovals convId="c1" />);
    act(() => pushLocalAgentEvent(pending("r1", "c1")));
    await waitFor(() => expect(shown("r1")).toBeTruthy());
    fireEvent.click(screen.getByText("Allow once"));
    expect((await screen.findByRole("alert")).textContent).toContain("Could not reach Ducky");
    expect(document.querySelector('[data-approval-request="r1"]')).toBeTruthy();
  });

  it("a window opened later shows the cards still waiting", async () => {
    _resetTerminalApprovalsForTests();
    api.terminal_pending_commands.mockResolvedValue([
      { request_id: "late", session_id: "s1", command: "npm ci", conv_id: "c1", rule_label: "npm ci", created_at: 1 },
    ]);
    render(<ChatTerminalApprovals convId="c1" />);
    await waitFor(() => expect(screen.getByText("npm ci")).toBeTruthy());
  });
});
