// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => ({
  api: {
    coding_agent_login: vi.fn(),
    coding_agent_login_status: vi.fn(),
    coding_agent_login_submit: vi.fn(),
    copy_text: vi.fn(),
  },
}));

vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => fake.api, isRemote: () => true }));

import { CodingAgentLoginModal } from "./CodingAgentsSection";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("Claude: the code box cleans the paste, refuses a pasted link and shows the CLI's own refusal", async () => {
  fake.api.coding_agent_login.mockResolvedValue({ ok: true, auth_url: "https://claude.ai/oauth/authorize?x=1" });
  fake.api.coding_agent_login_status.mockResolvedValue({ ok: true, logged_in: false });
  fake.api.coding_agent_login_submit.mockResolvedValue({ ok: false, logged_in: false, error: "Invalid code. Try again." });
  const onLoggedIn = vi.fn();
  render(<CodingAgentLoginModal agentId="claude_code" title="Log in to Claude Code" onClose={() => {}} onLoggedIn={onLoggedIn} />);

  const box = (await screen.findByPlaceholderText("Paste the login code")) as HTMLInputElement;
  expect(screen.getByText(/in the box below, not in the terminal/)).toBeTruthy();

  // The sign-in link pasted back is not a code: nothing goes to the CLI.
  fireEvent.change(box, { target: { value: "https://claude.ai/oauth/authorize?x=1" } });
  fireEvent.click(screen.getByText("Submit"));
  expect(await screen.findByText(/That is a link, not the code/)).toBeTruthy();
  expect(fake.api.coding_agent_login_submit).not.toHaveBeenCalled();

  // A copy with a line break and spaces around it goes in whole, both halves kept.
  fireEvent.change(box, { target: { value: "  abc123\n#state456 \n" } });
  fireEvent.click(screen.getByText("Submit"));
  await waitFor(() => expect(fake.api.coding_agent_login_submit).toHaveBeenCalledWith("claude_code", "abc123#state456"));
  expect(await screen.findByText("Invalid code. Try again.")).toBeTruthy();
  expect(onLoggedIn).not.toHaveBeenCalled();
});

it("shows Codex's one-time code to type at the link, with no box to paste a code into", async () => {
  fake.api.coding_agent_login.mockResolvedValue({
    ok: true,
    login_ui: "device_code",
    auth_url: "https://auth.openai.com/codex/device",
    user_code: "ABCD-EFG12",
  });
  fake.api.coding_agent_login_status.mockResolvedValue({ ok: true, logged_in: false });
  const onLoggedIn = vi.fn();
  render(<CodingAgentLoginModal agentId="codex" title="Log in to Codex" onClose={() => {}} onLoggedIn={onLoggedIn} />);

  expect(await screen.findByText("ABCD-EFG12")).toBeTruthy();
  expect(screen.getByText("https://auth.openai.com/codex/device")).toBeTruthy();
  expect(screen.queryByPlaceholderText("Paste the login code")).toBeNull();
  fireEvent.click(screen.getByText("Copy code"));
  expect(fake.api.copy_text).toHaveBeenCalledWith("ABCD-EFG12");

  // Signed in on the phone: the poll sees it and the modal finishes by itself.
  fake.api.coding_agent_login_status.mockResolvedValue({ ok: true, logged_in: true });
  await waitFor(() => expect(onLoggedIn).toHaveBeenCalled(), { timeout: 3000 });
});
