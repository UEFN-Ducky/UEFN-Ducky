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
  gateway_account_status: vi.fn(),
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
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
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
  await waitFor(() => expect(api.set_gateway_account_key).toHaveBeenCalledWith("anthropic", "new1", "sk-new", ""));
  expect(api.add_gateway_account).toHaveBeenCalledWith("anthropic", "Key 4", "key");
  expect(runBridgeJob).toHaveBeenCalledWith("test_key", ["anthropic", "sk-new", "", true], 300_000);
});

it("a key with a base URL is tested against it and saved with it", async () => {
  render(<GatewayAccounts provider="anthropic" kind="key" />);
  await screen.findByText("Team");
  fireEvent.click(screen.getByRole("button", { name: "Add API key" }));
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-proxy" } });
  fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://proxy.example/v1" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(api.set_gateway_account_key).toHaveBeenCalledWith("anthropic", "new1", "sk-proxy", "https://proxy.example/v1"));
  expect(runBridgeJob).toHaveBeenCalledWith("test_key", ["anthropic", "sk-proxy", "https://proxy.example/v1", true], 300_000);
});

it("Set base URL saves only the URL and shows it on the row", async () => {
  api.list_gateway_accounts.mockImplementation(async () => ({
    ok: true, auto_switch: true, default: "k1",
    accounts: keys.map((k) => (k.id === "k1" ? { ...k, base_url: "https://old.example/v1" } : k)),
  }));
  render(<GatewayAccounts provider="anthropic" kind="key" />);
  await screen.findByText("sk-…a1b2 · https://old.example/v1");
  fireEvent.click(screen.getByRole("button", { name: "More for Team" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Set base URL" }));
  expect(screen.queryByLabelText("API key")).toBeNull();
  const url = screen.getByLabelText("Base URL") as HTMLInputElement;
  expect(url.value).toBe("https://old.example/v1");
  fireEvent.change(url, { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.set_gateway_account_key).toHaveBeenCalledWith("anthropic", "k1", "", ""));
  expect(runBridgeJob).not.toHaveBeenCalled();
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

const lone = (status: { loggedIn: boolean; email?: string }) => ({
  ok: true, auto_switch: true, default: "default",
  // kind "key": what an older core reported for a lone CLI login; the table's kind wins.
  accounts: [{ id: "default", label: "Default", kind: "key", active: true, exhausted_until: null, status }],
});

it("one logged-out login shows Logged out and Log in", async () => {
  api.list_gateway_accounts.mockResolvedValue(lone({ loggedIn: false }));
  render(<GatewayAccounts provider="gemini_cli" kind="login" title="Gemini CLI logins" />);
  await screen.findByText("Not logged in");
  expect(within(row("Default")).getByText("Logged out")).toBeTruthy();
  expect(screen.queryByText("Uses the key above")).toBeNull();
  expect(screen.queryByRole("button", { name: "Make Default default" })).toBeNull();
  fireEvent.click(within(row("Default")).getByRole("button", { name: "Log in" }));
  await waitFor(() => expect(api.login_gateway_account).toHaveBeenCalledWith("gemini_cli", "default"));
});

it("one logged-in login shows Log out", async () => {
  api.list_gateway_accounts.mockResolvedValue(lone({ loggedIn: true, email: "me@x.com" }));
  render(<GatewayAccounts provider="gemini_cli" kind="login" />);
  await screen.findByText("me@x.com");
  expect(within(row("Default")).getByRole("button", { name: "Log out" })).toBeTruthy();
});

it("the default key row says No key yet when no key is saved", async () => {
  api.list_gateway_accounts.mockResolvedValue({
    ok: true, auto_switch: true, default: "default",
    accounts: [{ id: "default", label: "Default", kind: "key", active: true, exhausted_until: null, has_key: false }],
  });
  render(<GatewayAccounts provider="gemini" kind="key" />);
  await screen.findByText("No key yet");
  expect(within(row("Default")).getByText("No key")).toBeTruthy();
});

it("deleting the last account fades it out and shows the empty state", async () => {
  api.list_gateway_accounts.mockResolvedValue(lone({ loggedIn: true, email: "me@x.com" }));
  render(<GatewayAccounts provider="gemini_cli" kind="login" title="Gemini CLI logins" />);
  await screen.findByText("me@x.com");
  api.list_gateway_accounts.mockResolvedValue({ ok: true, auto_switch: true, default: "", accounts: [] });
  fireEvent.click(screen.getByRole("button", { name: "More for Default" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
  await waitFor(() => expect(row("Default").className).toContain("is-leaving"));
  expect(confirm.mock.calls[0][0].message).toBe("Delete Default? Its saved login is removed from this PC.");
  await screen.findByText("No Gemini CLI login yet");
  expect(api.remove_gateway_account).toHaveBeenCalledWith("gemini_cli", "default");
  expect(screen.queryByRole("button", { name: "Log in another account" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Log in" }));
  await waitFor(() => expect(api.login_gateway_account).toHaveBeenCalledWith("gemini_cli", "new1"));
});

it("reduced motion deletes at once with no leaving animation", async () => {
  const matchMedia = window.matchMedia;
  window.matchMedia = ((q: string) => ({ matches: q.includes("reduce") })) as unknown as typeof window.matchMedia;
  try {
    render(<GatewayAccounts provider="anthropic" kind="key" />);
    await screen.findByText("Team");
    fireEvent.click(screen.getByRole("button", { name: "More for Team" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    await waitFor(() => expect(api.remove_gateway_account).toHaveBeenCalledWith("anthropic", "k1"));
    expect(row("Team").className).not.toContain("is-leaving");
  } finally {
    window.matchMedia = matchMedia;
  }
});

it("a browser sign-in shows a spinner, not the terminal hint, then Logged in", async () => {
  api.list_gateway_accounts.mockResolvedValue(lone({ loggedIn: false }));
  api.login_gateway_account.mockResolvedValue({ ok: true, waiting: true, message: "Waiting for Google sign-in in your browser…" });
  api.gateway_account_status.mockReset().mockResolvedValue({ ok: true, status: { loggedIn: true, email: "you@gmail.com" } });
  render(<GatewayAccounts provider="gemini_cli" kind="login" />);
  await screen.findByText("Not logged in");
  fireEvent.click(within(row("Default")).getByRole("button", { name: "Log in" }));
  await screen.findByText("Waiting for Google sign-in in your browser…");
  expect(screen.queryByText(/terminal tab/)).toBeNull();
  await screen.findByText("Logged in · you@gmail.com", undefined, { timeout: 4000 });
  expect(api.gateway_account_status).toHaveBeenCalledWith("gemini_cli", "default");
});

it("a browser sign-in that times out says try again", async () => {
  api.list_gateway_accounts.mockResolvedValue(lone({ loggedIn: false }));
  api.login_gateway_account.mockResolvedValue({ ok: true, waiting: true, message: "Waiting…" });
  api.gateway_account_status.mockReset().mockResolvedValue({
    ok: true, status: { loggedIn: false, error: "Sign-in didn't finish, try again." } });
  render(<GatewayAccounts provider="gemini_cli" kind="login" />);
  await screen.findByText("Not logged in");
  fireEvent.click(within(row("Default")).getByRole("button", { name: "Log in" }));
  await waitFor(() => expect(screen.getByText("Sign-in didn't finish, try again.")).toBeTruthy(), { timeout: 4000 });
});

it("a terminal login still says to finish in the terminal tab", async () => {
  api.list_gateway_accounts.mockResolvedValue(lone({ loggedIn: false }));
  api.login_gateway_account.mockResolvedValue({ ok: true, session_id: "t1" });
  render(<GatewayAccounts provider="claude_code" kind="login" />);
  await screen.findByText("Not logged in");
  fireEvent.click(within(row("Default")).getByRole("button", { name: "Log in" }));
  await screen.findByText("Finish signing in in the terminal tab, then Refresh.");
});

it("no API key yet shows the empty state with Add API key", async () => {
  api.list_gateway_accounts.mockResolvedValue({ ok: true, auto_switch: true, default: "", accounts: [] });
  render(<GatewayAccounts provider="gemini" kind="key" title="API keys" />);
  await screen.findByText("No API key yet");
  fireEvent.click(screen.getByRole("button", { name: "Add API key" }));
  expect(screen.getByLabelText("API key")).toBeTruthy();
});
