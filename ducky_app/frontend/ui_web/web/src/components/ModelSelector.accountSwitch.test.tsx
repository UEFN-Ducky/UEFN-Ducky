// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({
  setAccount: vi.fn(async () => ({ ok: true })),
  setChatAccount: vi.fn(async () => ({ ok: true })),
  pluginCall: vi.fn(async () => ({})),
  accounts: [] as { id: string; label: string }[],
  chat: { account: "a1", label: "Work" },
  asked: [] as string[],
}));
const contrib = vi.hoisted(() => ({
  llm_providers: [{ id: "uefn_ducky", label: "Ducky AI", plugin_id: "account" }] as Record<string, unknown>[],
  llm_coding_agents: [] as Record<string, unknown>[],
}));
const sw = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));
const api = vi.hoisted(() => new Proxy({} as Record<string, unknown>, {
  get: (_t, key) => {
    if (key === "then") return undefined;
    if (key === "get_models_catalog") {
      return async () => ({ models: [{ id: "ducky-brain", name: "Ducky AI", provider: "uefn_ducky", provider_key: "uefn_ducky" }] });
    }
    if (key === "get_gateway_usage") return async () => ({ windows: [], switch: sw.current });
    if (key === "set_gateway_account") return calls.setAccount;
    if (key === "set_chat_account") return calls.setChatAccount;
    if (key === "list_gateway_accounts") {
      return async (p: string) => (calls.asked.push(p), { ok: true, auto_switch: true, accounts: calls.accounts });
    }
    if (key === "get_chat_account") {
      return async (_c: string, p: string) => (calls.asked.push(p), { ok: true, follows_default: false, ...calls.chat });
    }
    if (key === "gateway_account_usage") {
      return async (_p: string, id: string) => ({
        ok: true,
        windows: [{ id: "5h", label: `5-hour ${id}`, used: 10, limit: 100 }],
      });
    }
    if (key === "plugin_call") return calls.pluginCall;
    return async () => (/^(list|load)_/.test(String(key)) ? [] : {});
  },
}));
vi.mock("../hooks/usePanelApi", () => ({ getApi: () => api, isRemote: () => false, requireApi: () => api }));
vi.mock("../hooks/usePluginContributions", () => ({ usePluginContributions: () => contrib }));

import { ModelSelector } from "./ModelSelector";
import { loadModelsCatalog } from "../hooks/modelsCatalogCache";

const options = [
  { id: "default", label: "Default" },
  { id: "a1", label: "Work" },
  { id: "a2", label: "Spare" },
];

beforeEach(async () => {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  calls.setAccount.mockClear();
  calls.setChatAccount.mockClear();
  calls.pluginCall.mockClear();
  calls.accounts = options;
  calls.asked = [];
  contrib.llm_providers = [{ id: "uefn_ducky", label: "Ducky AI", plugin_id: "account" }];
  contrib.llm_coding_agents = [];
  await loadModelsCatalog({ force: true });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function openPicker(convId?: string) {
  const { container } = render(
    <ModelSelector selectedModel="ducky-brain" setSelectedModel={() => undefined} convId={convId}
                   codingAgent="ducky" setCodingAgent={() => undefined} preserveSelection />,
  );
  await act(async () => {});
  fireEvent.click(container.querySelector(".model-selector-btn")!);
  await act(async () => {});
}

it("core's account switch picks through set_gateway_account", async () => {
  sw.current = { plugin_id: "", rpc: "set_gateway_account", provider: "uefn_ducky", value: "default", options };
  await openPicker();
  fireEvent.click(await screen.findByRole("radio", { name: "Spare" }));
  await waitFor(() => expect(calls.setAccount).toHaveBeenCalledWith("uefn_ducky", "a2"));
  expect(calls.pluginCall).not.toHaveBeenCalled();
});

it("in a chat the switch shows and sets that chat's account only", async () => {
  sw.current = { plugin_id: "", rpc: "set_gateway_account", provider: "uefn_ducky", value: "default", options };
  await openPicker("chat-1");
  const work = await screen.findByRole("radio", { name: "Work" });
  await waitFor(() => expect(work.getAttribute("aria-checked")).toBe("true"));
  expect(await screen.findByText("5-hour a1")).toBeTruthy();
  fireEvent.click(screen.getByRole("radio", { name: "Spare" }));
  await waitFor(() => expect(calls.setChatAccount).toHaveBeenCalledWith("chat-1", "uefn_ducky", "a2"));
  expect(calls.setAccount).not.toHaveBeenCalled();
});

it("shows the chat's account number (no name) only when the gateway has 2+ accounts", async () => {
  sw.current = {};
  render(
    <ModelSelector selectedModel="ducky-brain" setSelectedModel={() => undefined} convId="chat-1"
                   codingAgent="ducky" setCodingAgent={() => undefined} preserveSelection />,
  );
  expect((await screen.findByLabelText("Account 2")).textContent).toBe("2");
  expect(screen.queryByText("Work")).toBeNull();
  cleanup();
  calls.accounts = [options[0]];
  render(
    <ModelSelector selectedModel="ducky-brain" setSelectedModel={() => undefined} convId="chat-1"
                   codingAgent="ducky" setCodingAgent={() => undefined} preserveSelection />,
  );
  await act(async () => {});
  expect(screen.queryByLabelText(/^Account \d/)).toBeNull();
});

it("a Claude Code chat uses Claude Code's logins, not the Anthropic API keys", async () => {
  contrib.llm_providers = [{ id: "anthropic", label: "Anthropic", plugin_id: "anthropic" }];
  contrib.llm_coding_agents = [{ id: "claude_code", label: "Claude Code", plugin_id: "anthropic" }];
  sw.current = {};
  render(
    <ModelSelector selectedModel="opus" setSelectedModel={() => undefined} convId="chat-1"
                   codingAgent="claude_code" setCodingAgent={() => undefined} preserveSelection />,
  );
  expect(await screen.findByLabelText("Account 2")).toBeTruthy();
  expect(calls.asked.length).toBeGreaterThan(0);
  expect(new Set(calls.asked)).toEqual(new Set(["claude_code"]));
});

it("a plugin's own switch still goes through plugin_call", async () => {
  sw.current = { plugin_id: "anthropic", rpc: "set_claude_account", value: "default", options };
  await openPicker();
  fireEvent.click(await screen.findByRole("radio", { name: "Work" }));
  await waitFor(() => expect(calls.pluginCall).toHaveBeenCalledWith("anthropic", "set_claude_account", { account: "a1" }));
  expect(calls.setAccount).not.toHaveBeenCalled();
});
