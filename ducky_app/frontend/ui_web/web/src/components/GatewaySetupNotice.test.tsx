// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => {
  const push = { listener: null as null | ((event: Record<string, unknown>) => void) };
  const walk = { active: false, listeners: new Set<() => void>() };
  return {
    push,
    walk,
    providers: [] as unknown[],
    status: vi.fn(),
    ensurePopularPlugins: vi.fn(),
    markStarterPluginToursCompleted: vi.fn(),
    markTourCompleted: vi.fn(),
    startTour: vi.fn(),
    reportFirstRunSetup: vi.fn(),
    openLlmsProviderSettings: vi.fn(),
  };
});

vi.mock("../hooks/onApiReady", () => ({
  onApiReady: (cb: (api: unknown) => void) => {
    cb({ starter_setup_status: fake.status });
    return () => {};
  },
}));
vi.mock("../hooks/usePanelApi", () => ({
  getApi: () => ({
    duckyos_store_catalog: () => Promise.resolve({ ok: true, items: [] }),
    get_key_status: () => Promise.resolve({}),
    list_coding_agents: () => Promise.resolve({ agents: [] }),
  }),
}));
vi.mock("../hooks/usePanelPushBus", () => ({
  installPanelPushBus: () => {},
  subscribePanelPush: (fn: (event: Record<string, unknown>) => void) => {
    fake.push.listener = fn;
    return () => {
      fake.push.listener = null;
    };
  },
}));
vi.mock("../hooks/storeInstallJobs", () => ({
  beginStoreInstall: () => {},
  clearStoreJobLater: () => {},
  endStoreInstall: () => {},
  patchStoreJob: () => {},
}));
vi.mock("../hooks/storeCatalogCache", () => ({ peekStoreCatalogCache: () => null, rememberStoreCatalog: () => {} }));
vi.mock("../hooks/usePluginContributions", () => ({
  usePluginContributions: () => ({ llm_providers: fake.providers, llm_coding_agents: [], ready: true }),
}));
vi.mock("../navigation/openSettingsTab", () => ({ openLlmsProviderSettings: fake.openLlmsProviderSettings }));
vi.mock("../walkthrough/firstRunSetup", () => ({ reportFirstRunSetup: fake.reportFirstRunSetup }));
vi.mock("../walkthrough/starterLlmGateways", () => ({
  ensurePopularPlugins: fake.ensurePopularPlugins,
  markStarterPluginToursCompleted: fake.markStarterPluginToursCompleted,
}));
vi.mock("../walkthrough/WalkthroughService", () => ({
  getWalkthroughState: () => ({ tourId: null, stepIndex: 0, active: fake.walk.active }),
  markTourCompleted: fake.markTourCompleted,
  startTour: fake.startTour,
  subscribeWalkthrough: (fn: () => void) => {
    fake.walk.listeners.add(fn);
    fn();
    return () => fake.walk.listeners.delete(fn);
  },
}));

const { GatewaySetupNotice, mergeRows, rowForPhase } = await import("./GatewaySetupNotice");

const BUNDLE = [
  { slug: "openai", label: "OpenAI", group: "gateway", installed: false },
  { slug: "verse", label: "Verse", group: "editor", installed: false },
];

function setWalk(active: boolean) {
  fake.walk.active = active;
  for (const fn of [...fake.walk.listeners]) fn();
}

beforeEach(() => {
  fake.providers = [];
  fake.walk.active = false;
  fake.walk.listeners.clear();
  fake.status.mockResolvedValue({ pending_first_run: true, gateway_ids: [], plugins: BUNDLE });
  fake.ensurePopularPlugins.mockResolvedValue(undefined);
  fake.startTour.mockResolvedValue(true);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("GatewaySetupNotice", () => {
  it("opens the setup on a new install and holds the Welcome tour", async () => {
    render(<GatewaySetupNotice />);
    expect(await screen.findByRole("dialog", { name: "Welcome to UEFN Ducky" })).toBeTruthy();
    expect(fake.reportFirstRunSetup).toHaveBeenCalledWith(true);
  });

  it("lets an existing install through: corner card, no setup, no hold", async () => {
    fake.status.mockResolvedValue({ pending_first_run: false, gateway_ids: [], plugins: BUNDLE });
    render(<GatewaySetupNotice />);
    expect(await screen.findByText("Finish setting up Ducky")).toBeTruthy();
    expect(fake.reportFirstRunSetup).toHaveBeenCalledWith(false);
    fireEvent.click(screen.getByRole("button", { name: "Continue setup" }));
    expect(screen.getByRole("dialog", { name: "Install the starter plugins" })).toBeTruthy();
  });

  it("installs the bundle and shows each plugin's progress", async () => {
    render(<GatewaySetupNotice />);
    fireEvent.click(await screen.findByRole("button", { name: /Get started/ }));
    fireEvent.click(screen.getByRole("button", { name: /Install 2 plugins/ }));
    expect(fake.ensurePopularPlugins).toHaveBeenCalledWith(true);
    act(() => {
      fake.push.listener?.({ type: "starter_plugins_progress", slug: "openai", label: "OpenAI", setup_phase: "installing" });
    });
    expect(document.querySelector(".setup-wizard-plugin.is-installing")?.textContent).toContain("OpenAI");
    act(() => {
      fake.push.listener?.({ type: "starter_plugins_progress", slug: "openai", label: "OpenAI", setup_phase: "installed" });
      fake.push.listener?.({ type: "starter_plugins_progress", slug: "verse", label: "Verse", setup_phase: "error", detail: "Offline" });
    });
    expect(document.querySelector(".setup-wizard-plugin.is-installed")?.textContent).toContain("OpenAI");
    expect(screen.getByText("Offline")).toBeTruthy();
  });

  it("marks a plugin failed from the job's result even when its progress event was missed", async () => {
    fake.ensurePopularPlugins.mockResolvedValue({ ok: false, errors: [{ slug: "verse", error: "Store is offline" }] });
    render(<GatewaySetupNotice />);
    fireEvent.click(await screen.findByRole("button", { name: /Get started/ }));
    fireEvent.click(screen.getByRole("button", { name: /Install 2 plugins/ }));
    expect(await screen.findByText("Store is offline")).toBeTruthy();
    expect(document.querySelector(".setup-wizard-plugin.is-error")?.textContent).toContain("Verse");
    expect(screen.getByRole("button", { name: /Try again/ })).toBeTruthy();
  });

  it("stays open when the gateways land, and Finish closes it and skips the chained tours", async () => {
    render(<GatewaySetupNotice />);
    fireEvent.click(await screen.findByRole("button", { name: /Get started/ }));
    fake.status.mockResolvedValue({
      pending_first_run: false,
      gateway_ids: ["openai"],
      plugins: BUNDLE.map((p) => ({ ...p, installed: true })),
    });
    act(() => {
      fake.push.listener?.({ type: "uefn_plugins_changed" });
    });
    fireEvent.click(await screen.findByRole("button", { name: /Next/ }));
    fireEvent.click(screen.getByRole("button", { name: /Finish/ }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fake.markStarterPluginToursCompleted).toHaveBeenCalled();
    expect(fake.markTourCompleted).toHaveBeenCalledWith("settings.store");
    expect(fake.markTourCompleted).toHaveBeenCalledWith("llms.setup");
    await waitFor(() => expect(fake.reportFirstRunSetup).toHaveBeenLastCalledWith(false));
  });

  it("steps aside for a provider tour and comes back to Connect AI when it ends", async () => {
    fake.providers = [{ id: "openai", label: "OpenAI", kind: "secret", secret_key: "openai", plugin_id: "openai" }];
    fake.status.mockResolvedValue({
      pending_first_run: true,
      gateway_ids: [],
      plugins: BUNDLE.map((p) => ({ ...p, installed: true })),
    });
    render(<GatewaySetupNotice />);
    fireEvent.click(await screen.findByRole("button", { name: /Get started/ }));
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Show me how" }));
    expect(fake.startTour).toHaveBeenCalledWith("plugin.openai", { force: true });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Back to setup" })).toBeTruthy();
    act(() => setWalk(true));
    expect(screen.queryByRole("dialog")).toBeNull();
    act(() => setWalk(false));
    expect(await screen.findByRole("dialog", { name: "Connect your AI" })).toBeTruthy();
  });

  it("opens the provider's Settings page when its plugin has no tour", async () => {
    fake.providers = [{ id: "openai", label: "OpenAI", kind: "secret", secret_key: "openai", plugin_id: "openai" }];
    fake.startTour.mockResolvedValue(false);
    fake.status.mockResolvedValue({
      pending_first_run: true,
      gateway_ids: [],
      plugins: BUNDLE.map((p) => ({ ...p, installed: true })),
    });
    render(<GatewaySetupNotice />);
    fireEvent.click(await screen.findByRole("button", { name: /Get started/ }));
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Show me how" }));
    await waitFor(() => expect(fake.openLlmsProviderSettings).toHaveBeenCalledWith("openai"));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Back to setup" }));
    expect(screen.getByRole("dialog", { name: "Connect your AI" })).toBeTruthy();
  });
});

describe("rows", () => {
  it("maps progress phases", () => {
    expect(rowForPhase("installing")).toEqual({ phase: "installing" });
    expect(rowForPhase("skipped")).toEqual({ phase: "installed" });
    expect(rowForPhase("error", "x")).toEqual({ phase: "error", detail: "x" });
    expect(rowForPhase("weird")).toBeNull();
  });

  it("keeps a row that is installing or failed until it lands", () => {
    const plugins = [
      { slug: "a", label: "A", group: "gateway" as const, installed: false },
      { slug: "b", label: "B", group: "editor" as const, installed: false },
      { slug: "c", label: "C", group: "editor" as const, installed: true },
    ];
    const next = mergeRows({ a: { phase: "installing" }, b: { phase: "error", detail: "x" } }, plugins);
    expect(next).toEqual({ a: { phase: "installing" }, b: { phase: "error", detail: "x" }, c: { phase: "installed" } });
  });
});
