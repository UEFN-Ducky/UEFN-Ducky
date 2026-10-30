// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { CodingAgentDto } from "../../types/panel";

const fake = vi.hoisted(() => ({
  agents: [] as unknown[],
  api: {
    list_coding_agents: vi.fn(),
    get_settings: vi.fn(),
    save_agent_settings: vi.fn(),
  },
}));

vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => fake.api, isRemote: () => true }));
vi.mock("../../hooks/onApiReady", () => ({ onApiReady: (fn: () => void) => (fn(), () => {}) }));
vi.mock("../../hooks/usePluginContributions", () => ({
  usePluginContributions: () => ({
    llm_coding_agents: [{ id: "claude_code", label: "Claude Code", plugin_id: "anthropic" }],
  }),
}));

import { ProviderCodingAgents } from "./CodingAgentsSection";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function setup(agent: Partial<CodingAgentDto>) {
  const row: CodingAgentDto = {
    id: "claude_code",
    label: "Claude Code",
    enabled: true,
    available: true,
    status: "Found: claude · v2.1.311 · logged in",
    capabilities: { needs_cli: true },
    ...agent,
  };
  fake.api.list_coding_agents.mockResolvedValue({ agents: [row] });
  fake.api.get_settings.mockResolvedValue({ coding_agents: {} });
  render(<ProviderCodingAgents pluginId="anthropic" />);
}

it("shows one tidy row and hides CLI inputs until Advanced opens", async () => {
  setup({ logged_in: true });
  expect(await screen.findByText("Logged in")).toBeTruthy();
  expect(screen.getByText("v2.1.311")).toBeTruthy();
  expect(screen.queryByPlaceholderText("CLI path (blank = PATH)")).toBeNull();
  expect(screen.queryByPlaceholderText("Default args")).toBeNull();

  const details = screen.getByText("Advanced").closest("details") as HTMLDetailsElement;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  expect(await screen.findByPlaceholderText("CLI path (blank = PATH)")).toBeTruthy();
  expect(screen.getByPlaceholderText("Default args")).toBeTruthy();
  expect(screen.getByText("Found: claude · v2.1.311 · logged in")).toBeTruthy();
});

it("opens Advanced by itself when the CLI is not found", async () => {
  setup({ available: false, status: "claude not found on PATH" });
  expect(await screen.findByText("Not found")).toBeTruthy();
  expect(screen.getByPlaceholderText("CLI path (blank = PATH)")).toBeTruthy();
});
