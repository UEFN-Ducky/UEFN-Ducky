// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StarterPluginDto } from "../types/panel";

const fake = vi.hoisted(() => {
  const api = {
    duckyos_store_catalog: vi.fn(),
    get_key_status: vi.fn(),
    list_coding_agents: vi.fn(),
    save_agent_settings: vi.fn(),
  };
  return { api, runBridgeJob: vi.fn(), providers: [] as unknown[] };
});

vi.mock("../hooks/usePanelApi", () => ({ getApi: () => fake.api }));
vi.mock("../hooks/bridgeJobAsync", () => ({ runBridgeJob: fake.runBridgeJob }));
vi.mock("../hooks/usePanelPushBus", () => ({ installPanelPushBus: () => {}, subscribePanelPush: () => () => {} }));
vi.mock("../hooks/storeCatalogCache", () => ({ peekStoreCatalogCache: () => null, rememberStoreCatalog: () => {} }));
vi.mock("../hooks/usePluginContributions", () => ({
  usePluginContributions: () => ({ llm_providers: fake.providers, llm_coding_agents: [], ready: true }),
}));

const { SetupWizard } = await import("./SetupWizard");

const PLUGINS: StarterPluginDto[] = [
  { slug: "openai", label: "OpenAI", group: "gateway", installed: false },
  { slug: "verse", label: "Verse", group: "editor", installed: true },
];

const OPENAI = {
  id: "openai",
  label: "OpenAI",
  kind: "secret" as const,
  secret_key: "openai",
  plugin_id: "openai",
  order: 20,
};

function wizard(over: Partial<Parameters<typeof SetupWizard>[0]> = {}) {
  const props = {
    step: "welcome" as const,
    onStep: vi.fn(),
    plugins: PLUGINS,
    rows: { openai: { phase: "ready" as const }, verse: { phase: "installed" as const } },
    installing: false,
    installError: "",
    onInstall: vi.fn(),
    onClose: vi.fn(),
    onShowMe: vi.fn(),
    ...over,
  };
  render(<SetupWizard {...props} />);
  return props;
}

beforeEach(() => {
  fake.providers = [];
  fake.api.duckyos_store_catalog.mockResolvedValue({
    ok: true,
    items: [{ slug: "openai", name: "OpenAI", description: "GPT models for chats. Also Codex.", icon_data_url: "data:image/png;base64,AA" }],
  });
  fake.api.get_key_status.mockResolvedValue({});
  fake.api.list_coding_agents.mockResolvedValue({ agents: [] });
  fake.api.save_agent_settings.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SetupWizard", () => {
  it("welcomes with the logo and the three steps", () => {
    const props = wizard();
    expect(screen.getByRole("dialog", { name: "Welcome to UEFN Ducky" })).toBeTruthy();
    expect(document.querySelector("img.setup-wizard-logo")?.getAttribute("src")).toBe("./OnlineMCPIcon.png");
    expect(screen.getByRole("list", { name: "Setup steps" }).textContent).toContain("Connect AI");
    fireEvent.click(screen.getByRole("button", { name: /Get started/ }));
    expect(props.onStep).toHaveBeenCalledWith("plugins");
    fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
    expect(props.onClose).toHaveBeenCalledWith("skip");
  });

  it("lists the plugins by group with their Store icon, line and status", async () => {
    const props = wizard({ step: "plugins" });
    expect(screen.getByRole("region", { name: "AI gateways" }).textContent).toContain("OpenAI");
    expect(screen.getByRole("region", { name: "UEFN editor tools" }).textContent).toContain("Verse");
    await waitFor(() => expect(screen.getByText("GPT models for chats.")).toBeTruthy());
    expect(document.querySelector(".setup-wizard-plugin.is-ready img")?.getAttribute("src")).toBe("data:image/png;base64,AA");
    expect(screen.getByText("1 of 2 installed")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Install 1 plugin$/ }));
    expect(props.onInstall).toHaveBeenCalled();
  });

  it("shows installing and failed rows, and Try again after a failure", () => {
    wizard({ step: "plugins", installing: true, rows: { openai: { phase: "installing" }, verse: { phase: "installed" } } });
    expect(screen.getByRole("button", { name: /Installing 2 of 2/ })).toHaveProperty("disabled", true);
    expect(document.querySelector(".setup-wizard-plugin.is-installing")?.textContent).toContain("Installing");
    cleanup();
    wizard({ step: "plugins", rows: { openai: { phase: "error", detail: "Store is offline" }, verse: { phase: "installed" } } });
    expect(screen.getByText("Store is offline")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Try again/ })).toBeTruthy();
  });

  it("goes on to the AI step once everything is installed", () => {
    const props = wizard({ step: "plugins", rows: { openai: { phase: "installed" }, verse: { phase: "installed" } } });
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    expect(props.onStep).toHaveBeenCalledWith("ai");
  });

  it("explains both ways to connect and saves a key that tests ok", async () => {
    fake.providers = [OPENAI];
    fake.runBridgeJob.mockResolvedValue({ ok: true, detail: "ok" });
    const props = wizard({ step: "ai" });
    expect(screen.getByText("API key")).toBeTruthy();
    expect(screen.getByText("Coding agent")).toBeTruthy();
    expect(screen.getByText("Not connected")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("OpenAI API key"), { target: { value: " sk-test " } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Test & save" }));
    });
    expect(fake.runBridgeJob).toHaveBeenCalledWith("test_key", ["openai", "sk-test"], 300_000);
    expect(fake.api.save_agent_settings).toHaveBeenCalledWith({ keys: { openai: "sk-test" } });
    expect(screen.getByText("Key saved. Duckies can use it now.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show me how" }));
    expect(props.onShowMe).toHaveBeenCalledWith(OPENAI);
    fireEvent.click(screen.getByRole("button", { name: /Finish/ }));
    expect(props.onClose).toHaveBeenCalledWith("finish");
  });

  it("keeps a key that fails the test out of settings", async () => {
    fake.providers = [OPENAI];
    fake.runBridgeJob.mockResolvedValue({ ok: false, detail: "Incorrect API key" });
    wizard({ step: "ai" });
    fireEvent.change(screen.getByLabelText("OpenAI API key"), { target: { value: "bad" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Test & save" }));
    });
    expect(screen.getByText("Incorrect API key")).toBeTruthy();
    expect(fake.api.save_agent_settings).not.toHaveBeenCalled();
  });

  it("shows a provider connected through its signed-in coding agent", async () => {
    fake.providers = [OPENAI];
    fake.api.list_coding_agents.mockResolvedValue({
      agents: [{ id: "codex", label: "Codex", plugin_id: "openai", available: true, logged_in: true, enabled: true, status: "ok" }],
    });
    wizard({ step: "ai" });
    await waitFor(() => expect(screen.getByText("Codex is signed in on this PC.")).toBeTruthy());
    expect(screen.getByText("Connected")).toBeTruthy();
  });

  it("sends people back to install when no gateway is there", () => {
    const props = wizard({ step: "ai" });
    fireEvent.click(screen.getByRole("button", { name: "Go back and install the plugins" }));
    expect(props.onStep).toHaveBeenCalledWith("plugins");
  });
});
