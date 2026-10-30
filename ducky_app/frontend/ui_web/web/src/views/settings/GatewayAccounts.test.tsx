// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = {
  list_gateway_accounts: vi.fn(),
  gateway_account_usage: vi.fn(),
  add_gateway_account: vi.fn(),
  remove_gateway_account: vi.fn(),
  set_gateway_account: vi.fn(),
  set_gateway_account_key: vi.fn(),
  login_gateway_account: vi.fn(),
};
const confirm = vi.fn();
const runBridgeJob = vi.fn();
vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => api }));
vi.mock("../../hooks/onApiReady", () => ({ onApiReady: (fn: () => void) => (fn(), () => {}) }));
vi.mock("../../contexts/ConfirmModalContext", () => ({ useConfirmModal: () => ({ confirm }) }));
vi.mock("../../hooks/bridgeJobAsync", () => ({ runBridgeJob }));
const { GatewayAccounts } = await import("./GatewayAccounts");

const logins = [
  { id: "default", label: "Personal", kind: "login", active: true, exhausted_until: null,
    status: { loggedIn: true, email: "you@x.com", plan: "Max" } },
  { id: "a1", label: "Work", kind: "login", active: false, exhausted_until: 1_900_000_000,
    status: { loggedIn: true, email: "work@y.com", plan: "Pro" } },
];
const keys = [
  { id: "default", label: "Main", kind: "key", active: false, exhausted_until: null, has_key: true },
  { id: "k1", label: "Team", kind: "key", active: true, exhausted_until: null, has_key: true, key_hint: "sk-…a1b2" },
  { id: "k2", label: "Spare", kind: "key", active: false, exhausted_until: null, has_key: false },
];

beforeEach(() => {
  api.list_gateway_accounts.mockReset().mockImplementation(async (provider: string) =>
    provider === "anthropic"
      ? { ok: true, auto_switch: true, default: "k1", accounts: keys }
      : { ok: true, auto_switch: true, default: "default", accounts: logins },
  );
  api.gateway_account_usage.mockReset().mockImplementation(async (_p: string, id: string) => ({
    ok: true,
    windows: [{ id: "5h", label: "5-hour", used: id === "a1" ? 100 : 8, limit: 100, reset: "Resets in 2h" }],
  }));
  for (const fn of [api.remove_gateway_account, api.set_gateway_account, api.set_gateway_account_key, api.login_gateway_account]) {
    fn.mockReset().mockResolvedValue({ ok: true });
  }
  api.add_gateway_account.mockReset().mockResolvedValue({ ok: true, id: "new1" });
  runBridgeJob.mockReset().mockResolvedValue({ ok: true });
  confirm.mockReset().mockResolvedValue(true);
});
afterEach(cleanup);

const row = (name: string) => screen.getByText(name).closest("tr") as HTMLElement;

it("shows login rows with email · plan, % left and status", async () => {
  render(<GatewayAccounts provider="claude_code" kind="login" title="Claude Code logins" />);
  await screen.findByText("you@x.com · Max");
  expect(screen.getByText("Claude Code logins (2)")).toBeTruthy();
  expect(screen.getByText("work@y.com · Pro")).toBeTruthy();
  await waitFor(() => expect(within(row("Personal")).getByText("92% left")).toBeTruthy());
  expect(within(row("Personal")).getByText("★ Default")).toBeTruthy();
  expect(within(row("Work")).getByText("0% left")).toBeTruthy();
  expect(within(row("Work")).getByText(/^Full until/)).toBeTruthy();
});

it("shows key rows masked and the missing key", async () => {
  render(<GatewayAccounts provider="anthropic" kind="key" title="API keys" />);
  await screen.findByText("sk-…a1b2");
  expect(within(row("Spare")).getByText("No key")).toBeTruthy();
  expect(within(row("Team")).getByText("★ Default")).toBeTruthy();
  // API keys have no plan limit: no Usage column, no switch-at-limit toggle.
  expect(screen.queryByRole("columnheader", { name: "Usage" })).toBeNull();
  expect(screen.queryByText("Switch accounts when one is at its limit")).toBeNull();
  expect(api.gateway_account_usage).not.toHaveBeenCalled();
});

it("the ⋯ menu opens outside the scrolling card", async () => {
  render(<GatewayAccounts provider="anthropic" kind="key" />);
  await screen.findByText("Team");
  fireEvent.click(screen.getByRole("button", { name: "More for Team" }));
  const menu = screen.getByRole("menu");
  expect(menu.closest(".gateway-accounts-card")).toBeNull();
  expect(within(menu).getByRole("menuitem", { name: "Replace key" })).toBeTruthy();
});

it("Make default calls set_gateway_account and moves the star", async () => {
  render(<GatewayAccounts provider="claude_code" kind="login" />);
  await screen.findByText("Work");
  api.list_gateway_accounts.mockResolvedValue({ ok: true, auto_switch: true, default: "a1", accounts: logins });
  fireEvent.click(screen.getByRole("button", { name: "Make Work default" }));
  await waitFor(() => expect(api.set_gateway_account).toHaveBeenCalledWith("claude_code", "a1"));
  await waitFor(() => expect(within(row("Personal")).queryByText("★ Default")).toBeNull());
  expect(screen.getByRole("button", { name: "Make Personal default" })).toBeTruthy();
});

it("asks before removing and only removes on yes", async () => {
  confirm.mockResolvedValueOnce(false);
  render(<GatewayAccounts provider="claude_code" kind="login" />);
  await screen.findByText("Work");
  const removeWork = () => {
    fireEvent.click(screen.getByRole("button", { name: "More for Work" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
  };
  removeWork();
  await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
  expect(api.remove_gateway_account).not.toHaveBeenCalled();
  removeWork();
  await waitFor(() => expect(api.remove_gateway_account).toHaveBeenCalledWith("claude_code", "a1"));
});

it("Add API key adds a row and saves the key on it", async () => {
  render(<GatewayAccounts provider="anthropic" kind="key" />);
  await screen.findByText("Team");
  fireEvent.click(screen.getByRole("button", { name: "Add API key" }));
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-new" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.set_gateway_account_key).toHaveBeenCalledWith("anthropic", "new1", "sk-new"));
  expect(api.add_gateway_account).toHaveBeenCalledWith("anthropic", "Key 4", "key");
});

it("Log in another account adds a login and starts its sign-in", async () => {
  render(<GatewayAccounts provider="claude_code" kind="login" />);
  await screen.findByText("Work");
  fireEvent.click(screen.getByRole("button", { name: "Log in another account" }));
  await waitFor(() => expect(api.login_gateway_account).toHaveBeenCalledWith("claude_code", "new1"));
  expect(api.add_gateway_account).toHaveBeenCalledWith("claude_code", "Account 3", "login");
});

it("an Anthropic page shows API keys and Claude Code logins tables alike", async () => {
  render(
    <>
      <GatewayAccounts provider="anthropic" kind="key" title="API keys" />
      <GatewayAccounts provider="claude_code" kind="login" title="Claude Code logins" />
    </>,
  );
  expect(await screen.findByText("API keys (3)")).toBeTruthy();
  expect(await screen.findByText("Claude Code logins (2)")).toBeTruthy();
  expect(screen.getAllByRole("table")).toHaveLength(2);
});
