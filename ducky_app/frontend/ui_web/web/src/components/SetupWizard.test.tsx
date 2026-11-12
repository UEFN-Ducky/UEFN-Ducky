// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StarterPluginDto } from "../types/panel";

const fake = vi.hoisted(() => {
  const api = {
    duckyos_store_catalog: vi.fn(),
    get_key_status: vi.fn(),
    list_coding_agents: vi.fn(),
    save_agent_settings: vi.fn(),
    get_settings: vi.fn(),
    get_ide_statuses: vi.fn(),
    apply_ide: vi.fn(),
    coding_agent_login: vi.fn(),
    coding_agent_login_status: vi.fn(),
    coding_agent_login_submit: vi.fn(),
    coding_agent_login_cancel: vi.fn(),
    detect_coding_agent_cli: vi.fn(),
    list_gateway_accounts: vi.fn(),
    login_gateway_account: vi.fn(),
    gateway_account_status: vi.fn(),
    duckyos_get_status: vi.fn(),
    duckyos_login: vi.fn(),
    duckyos_open_site: vi.fn(),
    duckyos_cancel_login: vi.fn(),
    get_memory_settings: vi.fn(),
    set_memory_settings: vi.fn(),
    open_external_url: vi.fn(),
  };
  return { api, runBridgeJob: vi.fn(), providers: [] as unknown[], saveVoice: vi.fn() };
});

vi.mock("../hooks/usePanelApi", () => ({ getApi: () => fake.api }));
vi.mock("../hooks/bridgeJobAsync", () => ({ runBridgeJob: fake.runBridgeJob }));
vi.mock("../hooks/usePanelPushBus", () => ({ installPanelPushBus: () => {}, subscribePanelPush: () => () => {} }));
vi.mock("../hooks/storeCatalogCache", () => ({
  peekStoreCatalogCache: () => null,
  rememberStoreCatalog: () => {},
  // No shared cache in these tests: every read goes to the fake API.
  fetchStoreCatalog: (api: { duckyos_store_catalog?: () => Promise<unknown> }) =>
    Promise.resolve(api?.duckyos_store_catalog?.() ?? null),
}));
vi.mock("../hooks/usePluginContributions", () => ({
  usePluginContributions: () => ({ llm_providers: fake.providers, llm_coding_agents: [], ready: true }),
}));
const CATALOG = vi.hoisted(() => [
  { provider: "Anthropic", providerKey: "anthropic", id: "claude-opus", name: "Claude Opus", supportsTools: true, supportsVision: true, supportsWebSearch: false, contextLimit: 0 },
  { provider: "Anthropic", providerKey: "anthropic", id: "claude-haiku", name: "Claude Haiku", supportsTools: true, supportsVision: true, supportsWebSearch: false, contextLimit: 0 },
]);
vi.mock("../hooks/modelsCatalogCache", () => ({
  refreshModelsCatalog: vi.fn(),
  rereadModelsCatalog: vi.fn(),
  getCachedModels: () => CATALOG,
  subscribeModelsCatalog: () => () => {},
}));
vi.mock("../voice/voiceSettings", () => ({
  loadVoiceSettings: () => Promise.resolve({ liveModel: "openai:gpt-mini", summaryModel: "" }),
  saveVoiceSettings: fake.saveVoice,
}));
vi.mock("./ducky/DuckyModelPicker", () => ({
  DuckyModelPicker: ({ model, onChange, placeholder }: { model: string; onChange: (m: string) => void; placeholder?: string }) => (
    <button type="button" onClick={() => onChange("anthropic:claude-sonnet")}>
      {model || placeholder}
    </button>
  ),
}));

const { SetupWizard, aiResults } = await import("./SetupWizard");

const PLUGINS: StarterPluginDto[] = [
  { slug: "openai", label: "OpenAI", group: "ai", installed: false },
  { slug: "uefn", label: "UEFN", group: "uefn", installed: true },
];

const OPENAI = {
  id: "openai",
  label: "OpenAI",
  kind: "secret" as const,
  secret_key: "openai",
  plugin_id: "openai",
  order: 20,
};
const ANTHROPIC = { ...OPENAI, id: "anthropic", label: "Anthropic", secret_key: "anthropic", plugin_id: "anthropic", order: 10 };
const CURSOR = { id: "cursor", label: "Cursor", kind: "url" as const, plugin_id: "cursor", order: 30 };

const AGENTS = [
  { id: "codex", label: "Codex", plugin_id: "openai", available: true, logged_in: true, enabled: true, status: "ok" },
  {
    id: "claude_code",
    label: "Claude Code",
    plugin_id: "anthropic",
    available: true,
    logged_in: false,
    can_login: true,
    enabled: true,
    status: "Not logged in",
  },
  {
    id: "cursor",
    label: "Cursor Agent",
    plugin_id: "cursor",
    available: false,
    logged_in: null,
    enabled: true,
    status: "CLI not found",
    install_help: "Install the Cursor CLI, then press Check.",
  },
  { id: "ducky", label: "Ducky", plugin_id: "", available: true, enabled: true, status: "ok" },
];

function wizard(over: Partial<Parameters<typeof SetupWizard>[0]> = {}) {
  const props = {
    step: "welcome" as const,
    onStep: vi.fn(),
    plugins: PLUGINS,
    rows: { openai: { phase: "ready" as const }, uefn: { phase: "installed" as const } },
    installing: false,
    installError: "",
    onInstall: vi.fn(),
    onFinish: vi.fn(),
    ...over,
  };
  render(<SetupWizard {...props} />);
  return props;
}

/** Open a provider's row (the first one that needs connecting starts open). */
function openRow(label: string) {
  const head = screen.getByRole("button", { name: new RegExp(`^${label}`) });
  if (head.getAttribute("aria-expanded") !== "true") fireEvent.click(head);
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
  fake.api.get_settings.mockResolvedValue({ default_model: "" });
  fake.api.get_ide_statuses.mockResolvedValue({});
  fake.api.apply_ide.mockResolvedValue("Applied");
  fake.api.coding_agent_login_status.mockResolvedValue({ ok: true, logged_in: false });
  fake.api.coding_agent_login_cancel.mockResolvedValue({ ok: true });
  fake.api.duckyos_get_status.mockResolvedValue({ logged_in: false });
  fake.api.duckyos_open_site.mockResolvedValue({ ok: true });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SetupWizard", () => {
  it("welcomes with the moving duckies and the four steps, and can't be skipped or closed", () => {
    const props = wizard();
    expect(screen.getByRole("dialog", { name: "Welcome to UEFN Ducky" })).toBeTruthy();
    expect(document.querySelector(".setup-scene image[href='./OnlineMCPIcon.png']")).toBeTruthy();
    expect(document.querySelectorAll(".setup-scene-duck")).toHaveLength(4);
    expect(screen.getByRole("list", { name: "Setup steps" }).textContent).toContain("Defaults");
    expect(screen.queryByRole("button", { name: /Skip/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Close setup" })).toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: /Get started/ }));
    expect(props.onStep).toHaveBeenCalledWith("plugins");
  });

  it("has no close button on any step", () => {
    for (const step of ["plugins", "ai", "defaults"] as const) {
      wizard({ step });
      expect(screen.queryByRole("button", { name: "Close setup" })).toBeNull();
      cleanup();
    }
  });

  it("finishes without a model or a where-you-heard pick", () => {
    const props = wizard({ step: "defaults" });
    const finish = screen.getByRole("button", { name: /Finish/ });
    expect(finish).toHaveProperty("disabled", false);
    fireEvent.click(finish);
    expect(props.onFinish).toHaveBeenCalled();
  });

  it("picks a job's model from its tiles in one click", async () => {
    fake.providers = [OPENAI];
    fake.api.get_key_status.mockResolvedValue({ openai: true });
    wizard({ step: "defaults", rows: { openai: { phase: "installed" }, uefn: { phase: "installed" } } });
    const chats = await screen.findByRole("listitem", { name: "Chats and duckies" });
    // The strongest model leads for chats, the small fast one for live voice.
    const first = within(chats).getAllByRole("radio")[0];
    expect(first.textContent).toContain("Claude Opus");
    fireEvent.click(first);
    expect(fake.api.save_agent_settings).toHaveBeenCalledWith({ default_model: "anthropic:claude-opus" });
    const live = screen.getByRole("listitem", { name: "Live voice" });
    expect(within(live).getAllByRole("radio")[1].textContent).toContain("Claude Haiku");
  });

  it("every step after Welcome has its own ducky that says something when clicked", () => {
    wizard({ step: "plugins" });
    fireEvent.click(screen.getByRole("button", { name: "Cowboy ducky" }));
    expect(screen.getByRole("status").textContent).toBe("Yeehaw, rounding up your plugins.");
    cleanup();
    wizard({ step: "defaults" });
    expect(screen.getByRole("button", { name: "Wizard ducky" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Pictures and 3D models" })).toBeNull();
  });

  it("a ducky says what it does when clicked", () => {
    wizard();
    expect(screen.getByText("Click a ducky to meet it")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Wizard ducky/ }));
    expect(screen.getByText("I write and fix your Verse.")).toBeTruthy();
    expect(document.querySelector(".setup-scene-job.is-picked")?.textContent).toContain("Verse");
  });

  it("keeps the long text behind a ? that shows on hover", () => {
    wizard();
    expect(screen.queryByText(/Never your chats/)).toBeNull();
    fireEvent.mouseEnter(screen.getByRole("button", { name: "About usage counts" }));
    expect(screen.getByRole("tooltip").textContent).toContain("Never your chats, files or project names");
    fireEvent.mouseLeave(screen.getByRole("button", { name: "About usage counts" }));
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("lists the plugins by group with their Store icon and a ? for the Store text", async () => {
    const props = wizard({ step: "plugins" });
    expect(screen.getByRole("region", { name: "AI models" }).textContent).toContain("OpenAI");
    expect(screen.getByRole("region", { name: "UEFN" }).textContent).toContain("UEFN");
    expect(screen.queryByRole("region", { name: "Art & 3D" })).toBeNull(); // no plugins, no group
    const about = await screen.findByRole("button", { name: "About OpenAI" });
    fireEvent.focus(about);
    expect(screen.getByRole("tooltip").textContent).toBe("GPT models for chats. Also Codex.");
    expect(document.querySelector(".setup-wizard-plugin.is-ready img")?.getAttribute("src")).toBe("data:image/png;base64,AA");
    expect(screen.getByLabelText("UEFN: Installed")).toBeTruthy();
    expect(screen.getByText("1 of 2")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Skip/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Install 1 plugin$/ }));
    expect(props.onInstall).toHaveBeenCalled();
  });

  it("waits while the AI plugins install, then lets you go on as the rest finish", () => {
    wizard({ step: "plugins", installing: true, rows: { openai: { phase: "installing" }, uefn: { phase: "ready" } } });
    expect(screen.getByRole("button", { name: /Installing 1 of 2/ })).toHaveProperty("disabled", true);
    expect(screen.getByLabelText("OpenAI: Installing")).toBeTruthy();
    cleanup();
    const props = wizard({ step: "plugins", installing: true, rows: { openai: { phase: "installed" }, uefn: { phase: "installing" } } });
    expect(screen.getByText(/1 more installing/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    expect(props.onStep).toHaveBeenCalledWith("ai");
  });

  it("a click that lands as Installing… turns into Next doesn't skip ahead", () => {
    vi.useFakeTimers();
    try {
      const props = wizard({ step: "plugins", installing: true, rows: { openai: { phase: "installing" } } });
      const ready = { ...props, rows: { openai: { phase: "installed" as const }, uefn: { phase: "installing" as const } } };
      cleanup();
      const { rerender } = render(<SetupWizard {...props} />);
      rerender(<SetupWizard {...ready} />);
      fireEvent.click(screen.getByRole("button", { name: /Next/ }));
      expect(props.onStep).not.toHaveBeenCalled();
      vi.advanceTimersByTime(700);
      fireEvent.click(screen.getByRole("button", { name: /Next/ }));
      expect(props.onStep).toHaveBeenCalledWith("ai");
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a failed plugin and Try again", () => {
    wizard({ step: "plugins", rows: { openai: { phase: "error", detail: "Store is offline" }, uefn: { phase: "installed" } } });
    expect(screen.getByText("Store is offline")).toBeTruthy();
    expect(screen.getByLabelText("OpenAI: Failed")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Try again/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Next/ })).toBeNull(); // no AI plugin yet
  });

  it("saves a key that tests ok, with no Show me how", async () => {
    fake.providers = [OPENAI];
    fake.runBridgeJob.mockResolvedValue({ ok: true, detail: "ok" });
    const props = wizard({ step: "ai" });
    expect(await screen.findByLabelText("OpenAI: Not connected")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("OpenAI API key"), { target: { value: " sk-test " } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Test & save" }));
    });
    expect(fake.runBridgeJob).toHaveBeenCalledWith("test_key", ["openai", "sk-test"], 300_000);
    expect(fake.api.save_agent_settings).toHaveBeenCalledWith({ keys: { openai: "sk-test" } });
    expect(screen.getByText("Key saved.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Show me how" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    expect(props.onStep).toHaveBeenCalledWith("defaults");
  });

  it("keeps a key that fails the test out of settings", async () => {
    fake.providers = [OPENAI];
    fake.runBridgeJob.mockResolvedValue({ ok: false, detail: "Incorrect API key" });
    wizard({ step: "ai" });
    fireEvent.change(await screen.findByLabelText("OpenAI API key"), { target: { value: "bad" } });
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
    await waitFor(() => expect(screen.getByLabelText("OpenAI: Connected")).toBeTruthy());
    openRow("OpenAI");
    expect(screen.getByLabelText("Codex: Logged in")).toBeTruthy();
  });

  it("opens one provider at a time, each with its key and its coding agents", async () => {
    fake.providers = [OPENAI, ANTHROPIC, CURSOR];
    fake.api.get_key_status.mockResolvedValue({ openai: true });
    fake.api.list_coding_agents.mockResolvedValue({ agents: AGENTS });
    wizard({ step: "ai" });
    await waitFor(() => expect(screen.getByLabelText("OpenAI: Connected")).toBeTruthy());
    expect(screen.getByLabelText("Anthropic: Not connected")).toBeTruthy();
    expect(screen.getByLabelText("Cursor: Not connected")).toBeTruthy();

    openRow("Anthropic");
    expect(screen.getAllByRole("button", { expanded: true })).toHaveLength(1);
    // Closed bodies stay mounted so they animate shut, but hidden from screen readers.
    expect(screen.getByLabelText("Ducky AI: Not connected").closest("li")?.querySelector(".setup-wizard-provider-clip")
      ?.getAttribute("aria-hidden")).toBe("true");
    const claude = screen.getByRole("list", { name: "Anthropic coding agents" });
    expect(within(claude).getByLabelText("Claude Code: Not logged in")).toBeTruthy();
    expect(within(claude).getByRole("button", { name: "Connect" })).toBeTruthy();
    expect(screen.getByLabelText("Anthropic API key")).toBeTruthy();

    openRow("Cursor");
    expect(screen.queryByLabelText("Cursor API key")).toBeNull(); // a key field only for API-key providers
    expect(screen.getByLabelText("Cursor Agent: Not installed")).toBeTruthy();
    fireEvent.mouseEnter(screen.getByRole("button", { name: "How to set up Cursor Agent" }));
    expect(screen.getByRole("tooltip").textContent).toBe("Install the Cursor CLI, then press Check.");
    expect(screen.queryByLabelText(/Ducky:/)).toBeNull(); // Ducky's own agent is not a login
  });

  it("Connect logs a plan in the way Settings does, then sets Ducky up in that app", async () => {
    fake.providers = [ANTHROPIC];
    let loggedIn = false;
    fake.api.list_coding_agents.mockImplementation(() =>
      Promise.resolve({ agents: [{ ...AGENTS[1]!, logged_in: loggedIn }] }),
    );
    fake.api.coding_agent_login.mockResolvedValue({ ok: true, auth_url: "https://claude.ai/oauth?x=1" });
    fake.api.coding_agent_login_submit.mockImplementation(() => {
      loggedIn = true;
      return Promise.resolve({ ok: true, logged_in: true });
    });
    let applied = false;
    fake.api.apply_ide.mockImplementation(() => {
      applied = true;
      return Promise.resolve("Applied");
    });
    fake.api.get_ide_statuses.mockImplementation(() => Promise.resolve(applied ? { claude: { ok: true, detail: "OK" } } : {}));
    wizard({ step: "ai" });
    fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
    expect(await screen.findByText("Log in to Claude Code")).toBeTruthy();
    await waitFor(() => expect(fake.api.coding_agent_login).toHaveBeenCalledWith("claude_code"));
    fireEvent.change(await screen.findByPlaceholderText("Paste the login code"), { target: { value: "abc#123" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    });
    expect(fake.api.coding_agent_login_submit).toHaveBeenCalledWith("claude_code", "abc#123");
    await waitFor(() => expect(screen.getByLabelText("Claude Code: Logged in")).toBeTruthy());
    expect(fake.api.apply_ide).toHaveBeenCalledWith("claude");
    expect(await screen.findByText(/Ducky.s tools are set up in Claude Code/)).toBeTruthy();
    expect(screen.getByLabelText("Anthropic: Connected")).toBeTruthy();
    expect(screen.queryByText("Log in to Claude Code")).toBeNull();
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });

  const DEFAULT_ACCOUNT = { ok: true, accounts: [{ id: "default", label: "Default", kind: "login", active: true }], default: "default", auto_switch: true };

  it("Connect signs a plan in with one click: the browser, no window to fill in, then Ducky's tools", async () => {
    fake.providers = [OPENAI];
    let loggedIn = false;
    fake.api.list_coding_agents.mockImplementation(() =>
      Promise.resolve({ agents: [{ ...AGENTS[0]!, logged_in: loggedIn, can_login: true, one_click_login: true }] }),
    );
    fake.api.list_gateway_accounts.mockResolvedValue(DEFAULT_ACCOUNT);
    fake.api.login_gateway_account.mockResolvedValue({ ok: true, waiting: true, message: "Sign in to ChatGPT in the browser tab that just opened…" });
    fake.api.gateway_account_status
      .mockResolvedValueOnce({ ok: true, status: { loggedIn: false, pending: true } })
      .mockImplementation(() => {
        loggedIn = true;
        return Promise.resolve({ ok: true, status: { loggedIn: true, email: "a@b.c" } });
      });
    fake.api.detect_coding_agent_cli.mockResolvedValue({ ok: true, available: true });
    wizard({ step: "ai" });
    fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
    expect(await screen.findByText("Sign in to ChatGPT in the browser tab that just opened…")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Connecting…" }) as HTMLButtonElement).disabled).toBe(true);
    await waitFor(() => expect(screen.getByLabelText("Codex: Logged in")).toBeTruthy(), { timeout: 8000 });
    expect(fake.api.login_gateway_account).toHaveBeenCalledWith("codex", "default");
    expect(fake.api.coding_agent_login).not.toHaveBeenCalled();
    expect(screen.queryByText("Log in to Codex")).toBeNull();
    await waitFor(() => expect(fake.api.apply_ide).toHaveBeenCalledWith("codex"));
    expect(screen.queryByText("Sign in to ChatGPT in the browser tab that just opened…")).toBeNull();
  }, 15000);

  it("Connect on a program that isn't installed installs it, then signs in by itself", async () => {
    fake.providers = [CURSOR];
    let state: "missing" | "installed" | "in" = "missing";
    fake.api.list_coding_agents.mockImplementation(() =>
      Promise.resolve({
        agents: [{ ...AGENTS[2]!, one_click_login: true, available: state !== "missing", logged_in: state === "missing" ? null : state === "in" }],
      }),
    );
    fake.api.list_gateway_accounts.mockResolvedValue(DEFAULT_ACCOUNT);
    fake.api.login_gateway_account
      .mockResolvedValueOnce({ ok: false, error: "Cursor isn't installed yet. Ducky is installing it; sign-in starts by itself." })
      .mockResolvedValue({ ok: true, waiting: true, message: "Sign in to Cursor in the browser tab that just opened…" });
    fake.api.gateway_account_status
      .mockResolvedValueOnce({ ok: true, status: { loggedIn: false, pending: true, installing: true } })
      .mockImplementationOnce(() => {
        state = "installed";
        return Promise.resolve({ ok: true, status: { loggedIn: false } });
      })
      .mockImplementation(() => {
        state = "in";
        return Promise.resolve({ ok: true, status: { loggedIn: true } });
      });
    fake.api.detect_coding_agent_cli.mockResolvedValue({ ok: true, available: true });
    wizard({ step: "ai" });
    expect(await screen.findByLabelText("Cursor Agent: Not installed")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Check" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByText(/^Installing it/)).toBeTruthy();
    await waitFor(() => expect(fake.api.login_gateway_account).toHaveBeenCalledTimes(2), { timeout: 8000 });
    await waitFor(() => expect(screen.getByLabelText("Cursor Agent: Logged in")).toBeTruthy(), { timeout: 8000 });
    expect(fake.api.apply_ide).toHaveBeenCalledWith("cursor");
    expect(screen.queryByText(/isn't installed yet/)).toBeNull();
  }, 20000);

  it("a failed install says why, on the row", async () => {
    fake.providers = [CURSOR];
    fake.api.list_coding_agents.mockResolvedValue({ agents: [{ ...AGENTS[2]!, one_click_login: true }] });
    fake.api.list_gateway_accounts.mockResolvedValue(DEFAULT_ACCOUNT);
    fake.api.login_gateway_account.mockResolvedValue({ ok: false, error: "Cursor isn't installed yet." });
    fake.api.gateway_account_status
      .mockResolvedValueOnce({ ok: true, status: { loggedIn: false, pending: true, installing: true } })
      .mockResolvedValue({ ok: true, status: { loggedIn: false, error: "Cursor install failed: no internet" } });
    wizard({ step: "ai" });
    fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
    expect(await screen.findByText("Cursor install failed: no internet", {}, { timeout: 8000 })).toBeTruthy();
    expect(fake.api.login_gateway_account).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Connect" })).toBeTruthy();
  }, 15000);

  it("Claude Code that isn't installed: Connect installs it, then opens its sign-in", async () => {
    fake.providers = [ANTHROPIC];
    let installed = false;
    fake.api.list_coding_agents.mockImplementation(() =>
      Promise.resolve({ agents: [{ ...AGENTS[1]!, available: installed, logged_in: installed ? false : null, status: installed ? "Not logged in" : "CLI not found" }] }),
    );
    fake.api.list_gateway_accounts.mockResolvedValue(DEFAULT_ACCOUNT);
    fake.api.detect_coding_agent_cli.mockResolvedValue({ ok: true, available: false, status: "Installing Claude Code CLI…" });
    fake.api.gateway_account_status
      .mockResolvedValueOnce({ ok: true, status: { loggedIn: false, pending: true, installing: true } })
      .mockImplementation(() => {
        installed = true;
        return Promise.resolve({ ok: true, status: { loggedIn: false } });
      });
    fake.api.coding_agent_login.mockResolvedValue({ ok: true, auth_url: "https://claude.ai/oauth?x=1" });
    wizard({ step: "ai" });
    expect(await screen.findByLabelText("Claude Code: Not installed")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    expect(await screen.findByText("Log in to Claude Code", {}, { timeout: 8000 })).toBeTruthy();
    expect(fake.api.detect_coding_agent_cli).toHaveBeenCalledWith("claude_code");
    expect(fake.api.login_gateway_account).not.toHaveBeenCalled(); // that one opens a terminal tab for Claude Code
    await waitFor(() => expect(fake.api.coding_agent_login).toHaveBeenCalledWith("claude_code"));
  }, 15000);

  it("checks an agent again after it was installed or signed in elsewhere", async () => {
    fake.providers = [CURSOR];
    let installed = false;
    fake.api.list_coding_agents.mockImplementation(() =>
      Promise.resolve({ agents: [{ ...AGENTS[2]!, available: installed, logged_in: installed ? true : null }] }),
    );
    fake.api.detect_coding_agent_cli.mockImplementation(() => {
      installed = true;
      return Promise.resolve({ ok: true });
    });
    wizard({ step: "ai" });
    fireEvent.click(await screen.findByRole("button", { name: "Check" }));
    await waitFor(() => expect(screen.getByLabelText("Cursor Agent: Logged in")).toBeTruthy());
    expect(fake.api.detect_coding_agent_cli).toHaveBeenCalledWith("cursor");
    expect(screen.getByLabelText("Cursor: Connected")).toBeTruthy();
  });

  it("sends people back to install when no AI plugin is there", () => {
    const props = wizard({ step: "ai" });
    fireEvent.click(screen.getByRole("button", { name: "Back to plugins" }));
    expect(props.onStep).toHaveBeenCalledWith("plugins");
  });

  it("sets the default AI, sums up, and finishes", async () => {
    fake.providers = [OPENAI];
    fake.api.get_key_status.mockResolvedValue({ openai: true });
    fake.api.get_ide_statuses.mockResolvedValue({ codex: { ok: true, detail: "OK" } });
    const props = wizard({ step: "defaults", rows: { openai: { phase: "installed" }, uefn: { phase: "installed" } } });

    fireEvent.click(await screen.findByRole("button", { name: "Pick a model" }));
    expect(fake.api.save_agent_settings).toHaveBeenCalledWith({ default_model: "anthropic:claude-sonnet" });
    // Every job has its own picker with a suggestion, saved where Settings keeps it.
    expect(within(screen.getByRole("listitem", { name: "Live voice" })).getByText(/Try Claude Haiku/)).toBeTruthy();
    expect(await screen.findByRole("button", { name: "openai:gpt-mini" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Chats model" }));
    expect(fake.saveVoice).toHaveBeenCalledWith({ summaryModel: "anthropic:claude-sonnet" });
    fireEvent.click(screen.getByRole("button", { name: "Spoken summaries model" }));
    expect(fake.api.set_memory_settings).toHaveBeenCalledWith({ memory_summary_model: "anthropic:claude-sonnet" });
    expect(screen.queryByRole("listitem", { name: "Pictures" })).toBeNull(); // no picture / 3D maker pickers in setup
    expect(screen.getByText("Default AI: claude-sonnet")).toBeTruthy();
    expect(screen.getByText("AI: OpenAI")).toBeTruthy();
    expect(screen.getByText("Plugins: 2 of 2")).toBeTruthy();
    expect(await screen.findByText("Ducky's tools in: Codex")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Use Ducky in other apps" })).toBeNull();

    const finish = screen.getByRole("button", { name: /Finish/ });
    fireEvent.click(screen.getByRole("checkbox", { name: "YouTube" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "A friend" }));
    expect(screen.getByRole("checkbox", { name: "YouTube" }).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(finish);
    expect(props.onFinish).toHaveBeenCalled();
  });

  it("signs this PC in to Ducky AI: the code's page opens, then Ducky AI is connected", async () => {
    let approve: (s: unknown) => void = () => {};
    fake.api.duckyos_login.mockReturnValue(new Promise((r) => (approve = r)));
    fake.api.duckyos_get_status.mockResolvedValue({ logged_in: false, browser_pending: true, user_code: "abcd 1234" });
    wizard({ step: "ai" });
    // Ducky AI is a row like the providers, open while there is none: optional, and what it unlocks.
    expect(screen.getByRole("button", { name: /^Ducky AI Optional/ }).getAttribute("aria-expanded")).toBe("true");
    expect(document.querySelector(".setup-wizard-provider-logo img")?.getAttribute("src")).toBe("./OnlineMCPIcon.png");
    expect(screen.getByText(/^Optional\./)).toBeTruthy();
    expect(screen.getByText(/Team features: source control/)).toBeTruthy();
    expect(screen.getByText(/The phone app/)).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Sign in or sign up" }));
    await waitFor(() => expect(fake.api.duckyos_open_site).toHaveBeenCalledWith("/connect?code=ABCD1234"), { timeout: 3000 });
    expect(screen.getByText("Name this PC in your browser (code abcd 1234).")).toBeTruthy();
    expect(fake.api.duckyos_open_site).toHaveBeenCalledOnce(); // the page opens once per code

    fake.api.duckyos_get_status.mockResolvedValue({ logged_in: true, email: "me@ducky.test" });
    await act(async () => approve({ ok: true, logged_in: true, email: "me@ducky.test" }));
    expect(await screen.findByLabelText("Ducky AI: Connected")).toBeTruthy();
    expect(screen.getByText("Signed in as me@ducky.test. This PC is connected.")).toBeTruthy();
  });

  it("a plan already logged in connects by setting Ducky up in that app", async () => {
    fake.providers = [OPENAI];
    fake.api.list_coding_agents.mockResolvedValue({ agents: [AGENTS[0]] });
    wizard({ step: "ai" });
    await waitFor(() => expect(screen.getByLabelText("Codex: Logged in")).toBeTruthy());
    expect(screen.getByRole("region", { name: "OpenAI: your plan" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "OpenAI: API key" }).textContent).toContain("Or an API key");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    });
    expect(fake.api.apply_ide).toHaveBeenCalledWith("codex");
  });

  it("a provider whose plugin says key first shows the API key before the plan, with a Get a key link", async () => {
    // Google: Gemini CLI refuses personal accounts, so the key is the way that works (Nov 10 2026).
    fake.providers = [{
      id: "gemini", label: "Google", kind: "secret" as const, secret_key: "gemini", plugin_id: "google", order: 30,
      key_url: "https://aistudio.google.com/apikey", key_first: true,
    }];
    fake.api.list_coding_agents.mockResolvedValue({
      agents: [{ id: "gemini_cli", label: "Gemini CLI", plugin_id: "google", available: true, logged_in: false, enabled: true, status: "" }],
    });
    wizard({ step: "ai" });
    const key = await screen.findByRole("region", { name: "Google: API key", hidden: true });
    const plan = await screen.findByRole("region", { name: "Google: your plan", hidden: true });
    expect(key.compareDocumentPosition(plan) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(key.textContent).not.toContain("Or an API key");
    expect(plan.textContent).toContain("Or your plan");
    fireEvent.click(screen.getByRole("button", { name: "Get a key", hidden: true }));
    expect(fake.api.open_external_url).toHaveBeenCalledWith("https://aistudio.google.com/apikey");
  });

  it("asks for an AI before a default can be picked", async () => {
    const props = wizard({ step: "defaults" });
    expect(await screen.findByText("AI: not connected")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Pick a model" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Connect AI" }));
    expect(props.onStep).toHaveBeenCalledWith("ai");
  });
});

describe("aiResults (Connect AI at Finish)", () => {
  const provider = (plugin_id: string) => ({ id: plugin_id, label: plugin_id, kind: "secret" as const, secret_key: plugin_id + "_key", plugin_id });
  const agent = (over: Record<string, unknown>) => ({ id: "a", label: "A", available: true, status: "", ...over }) as never;

  it("names each AI and how it ended up connected", () => {
    const providers = [provider("anthropic"), provider("openai"), provider("google"), provider("cursor"), provider("somegateway")];
    const agents: Record<string, unknown[]> = {
      anthropic: [agent({ logged_in: true })],
      openai: [agent({ logged_in: false })],
      google: [agent({ available: false })],
      cursor: [],
      somegateway: [],
    };
    const results = aiResults(providers, { cursor_key: true }, (p) => agents[p.plugin_id] as never, true);
    expect(results).toEqual([
      { provider: "claude", state: "plan" },
      { provider: "codex", state: "not_logged_in" },
      { provider: "gemini", state: "not_installed" },
      { provider: "cursor", state: "key" },
      { provider: "other", state: "none" },
      { provider: "ducky_ai", state: "account" },
    ]);
  });

  it("recommends Ducky AI when no coding CLI is installed, and not once one is", async () => {
    const missing = (a: (typeof AGENTS)[number]) => ({ ...a, available: false, logged_in: null, status: "CLI not found" });
    fake.providers = [OPENAI, ANTHROPIC];
    fake.api.list_coding_agents.mockResolvedValue({ agents: [missing(AGENTS[0]!), missing(AGENTS[1]!)] });
    wizard({ step: "ai" });
    expect(await screen.findByText("Recommended: nothing to install")).toBeTruthy();
    cleanup();

    fake.api.list_coding_agents.mockResolvedValue({ agents: [AGENTS[0], missing(AGENTS[1]!)] });
    wizard({ step: "ai" });
    await waitFor(() => expect(screen.getByLabelText("OpenAI: Connected")).toBeTruthy());
    expect(screen.queryByText("Recommended: nothing to install")).toBeNull();
  });

  it("reports Ducky AI as not connected when the PC isn't signed in", () => {
    expect(aiResults([], {}, () => [], false)).toEqual([{ provider: "ducky_ai", state: "none" }]);
  });
});
