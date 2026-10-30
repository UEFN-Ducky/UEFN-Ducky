// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = {
  list_gateway_accounts: vi.fn(),
  gateway_account_status: vi.fn(),
  add_gateway_account: vi.fn(),
  remove_gateway_account: vi.fn(),
  set_gateway_account: vi.fn(),
};
const confirm = vi.fn();
vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../../hooks/onApiReady", () => ({ onApiReady: (fn: () => void) => (fn(), () => {}) }));
vi.mock("../../contexts/ConfirmModalContext", () => ({ useConfirmModal: () => ({ confirm }) }));
const { GatewayAccounts } = await import("./GatewayAccounts");

beforeEach(() => {
  api.list_gateway_accounts.mockReset().mockResolvedValue({
    ok: true,
    auto_switch: true,
    accounts: [
      { id: "default", label: "Default", kind: "login", active: true, exhausted_until: null },
      { id: "a1", label: "Work", kind: "login", active: false, exhausted_until: 1_900_000_000 },
    ],
  });
  api.gateway_account_status.mockReset().mockImplementation(async (_p: string, id: string) => ({
    ok: true,
    status: id === "default"
      ? { loggedIn: true, email: "you@x.com", plan: "Max" }
      : { loggedIn: true, email: "work@y.com", plan: "Pro" },
  }));
  for (const fn of [api.add_gateway_account, api.remove_gateway_account, api.set_gateway_account]) {
    fn.mockReset().mockResolvedValue({ ok: true });
  }
  confirm.mockReset().mockResolvedValue(true);
});
afterEach(cleanup);

it("lists every account with its login and limit", async () => {
  render(<GatewayAccounts provider="claude_code" kind="login" />);
  await screen.findByText("Work");
  expect(screen.getByText("Default")).toBeTruthy();
  await screen.findByText("Logged in as you@x.com · Max");
  expect(screen.getByText(/^Logged in as work@y\.com · Pro · at its limit until/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Remove Default" })).toBeNull();
});

it("adds a login", async () => {
  render(<GatewayAccounts provider="claude_code" kind="login" />);
  await screen.findByText("Work");
  fireEvent.click(screen.getByRole("button", { name: "Add login" }));
  await waitFor(() => expect(api.add_gateway_account).toHaveBeenCalledWith("claude_code", "Account 3", "login"));
});

it("asks before removing and only removes on yes", async () => {
  confirm.mockResolvedValueOnce(false);
  render(<GatewayAccounts provider="claude_code" kind="login" />);
  await screen.findByText("Work");
  fireEvent.click(screen.getByRole("button", { name: "Remove Work" }));
  await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
  expect(api.remove_gateway_account).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Remove Work" }));
  await waitFor(() => expect(api.remove_gateway_account).toHaveBeenCalledWith("claude_code", "a1"));
});

it("picks the active account with the radio", async () => {
  render(<GatewayAccounts provider="openai" kind="key" />);
  await screen.findByText("Work");
  fireEvent.click(screen.getByRole("radio", { name: "Use Work" }));
  await waitFor(() => expect(api.set_gateway_account).toHaveBeenCalledWith("openai", "a1"));
  expect(screen.getByRole("button", { name: "Add API key" })).toBeTruthy();
});
