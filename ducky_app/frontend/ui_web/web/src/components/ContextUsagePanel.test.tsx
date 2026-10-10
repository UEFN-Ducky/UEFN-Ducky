// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ContextUsage } from "../types/panel";

const api = {
  get_context_usage: vi.fn(async () => ({ breakdown: [] })),
  set_agent_allow_everything: vi.fn(async () => ({ ok: true, on: false, own: false, from_title: "" })),
  get_agent_permissions: vi.fn(async () => ({ mode: "edits", label: "Accept edits", own: true, from_title: "", asks: true })),
};
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("./ChangesetPanel", () => ({ ChangesetPanel: () => null }));

import { ConfirmModalProvider } from "../contexts/ConfirmModalContext";
import { announceChatPermissions } from "./chatPermissionsSync";
import { ContextUsagePanel } from "./ContextUsagePanel";

type Approvals = { mode: "ask" | "edits" | "all"; label: string; asks: boolean };

function usage(allow: { on: boolean; own: boolean; from_title?: string }, approvals?: Approvals): ContextUsage {
  return {
    used_tokens: 1000, context_limit: 200000, input_tokens: 0, breakdown: [],
    agent_info: { coding_agent: "claude_code", label: "Claude Code", model: "claude-opus-5-5", enabled: true, session_active: true,
                  num_turns: 1, context_tokens: 1000, has_run: true, permission_mode: "acceptEdits", allow_everything: allow, approvals },
  } as unknown as ContextUsage;
}

const show = (allow: { on: boolean; own: boolean; from_title?: string }, approvals?: Approvals) => render(
  <ConfirmModalProvider><ContextUsagePanel convId="c1" usage={usage(allow, approvals)} sessionFiles={[]} onClose={() => undefined} /></ConfirmModalProvider>,
);

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("Approvals row", () => {
  it("shows Allow everything set in this chat and turns it off", async () => {
    show({ on: true, own: true });
    expect(screen.getByText("Allow everything (never asks)")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(api.set_agent_allow_everything).toHaveBeenCalledWith("c1", false));
    // Back to the chat's own mode, as its permissions button shows it.
    await waitFor(() => expect(screen.getByText("Accept edits")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Turn off" })).toBeNull();
  });

  it("names the chat it came from, without a switch here", () => {
    show({ on: true, own: false, from_title: "Card art lead" });
    expect(screen.getByText("Allow everything, from Card art lead")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Turn off" })).toBeNull();
  });

  it("shows nothing while the chat asks", () => {
    show({ on: false, own: false });
    expect(screen.queryByText("Approvals")).toBeNull();
  });

  it("shows the chat's mode in the words of its permissions button", () => {
    show({ on: false, own: false }, { mode: "ask", label: "Ask before changes", asks: true });
    expect(screen.getByText("Approvals")).toBeTruthy();
    expect(screen.getByText("Ask before changes")).toBeTruthy();
  });

  it("says an agent that cannot ask never asks", () => {
    show({ on: false, own: false }, { mode: "edits", label: "Accept edits", asks: false });
    expect(screen.getByText("Never asks")).toBeTruthy();
  });

  it("follows a mode picked from the permissions button", async () => {
    show({ on: false, own: false }, { mode: "edits", label: "Accept edits", asks: true });
    expect(screen.getByText("Accept edits")).toBeTruthy();
    api.get_agent_permissions.mockResolvedValueOnce({ mode: "all", label: "Allow everything", own: true, from_title: "", asks: true });
    announceChatPermissions("c1", {});
    await waitFor(() => expect(screen.getByText("Allow everything (never asks)")).toBeTruthy());
    expect(api.get_agent_permissions).toHaveBeenCalledWith("c1", "claude_code");
    expect(screen.getByRole("button", { name: "Turn off" })).toBeTruthy();
  });
});
