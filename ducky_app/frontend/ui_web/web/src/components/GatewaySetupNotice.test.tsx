// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => {
  const push = { listener: null as null | ((event: Record<string, unknown>) => void) };
  return {
    push,
    providers: [] as unknown[],
    status: vi.fn(),
    ensurePopularPlugins: vi.fn(),
    markStarterPluginToursCompleted: vi.fn(),
    markTourCompleted: vi.fn(),
    reportFirstRunSetup: vi.fn(),
    welcomeAfterSetup: vi.fn(),
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
    get_settings: () => Promise.resolve({}),
    get_ide_statuses: () => Promise.resolve({}),
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
vi.mock("../hooks/modelsCatalogCache", () => ({
  refreshModelsCatalog: vi.fn(),
  getCachedModels: () => [],
  subscribeModelsCatalog: () => () => {},
}));
vi.mock("../hooks/usePluginContributions", () => ({
  usePluginContributions: () => ({ llm_providers: fake.providers, llm_coding_agents: [], ready: true }),
}));
vi.mock("../walkthrough/firstRunSetup", () => ({ reportFirstRunSetup: fake.reportFirstRunSetup }));
vi.mock("../walkthrough/starterLlmGateways", () => ({
  ensurePopularPlugins: fake.ensurePopularPlugins,
  markStarterPluginToursCompleted: fake.markStarterPluginToursCompleted,
}));
vi.mock("../walkthrough/welcomeDucky", () => ({ welcomeAfterSetup: fake.welcomeAfterSetup }));
vi.mock("../walkthrough/WalkthroughService", () => ({ markTourCompleted: fake.markTourCompleted }));

const { GatewaySetupNotice, SetupReminder, mergeRows, rowForPhase } = await import("./GatewaySetupNotice");

const BUNDLE = [
  { slug: "openai", label: "OpenAI", group: "ai", installed: false },
  { slug: "uefn", label: "UEFN", group: "uefn", installed: false },
];

// The wizard's main button ignores a click right after it changes (a double click):
// pause() moves the clock past that.
const realNow = Date.now.bind(Date);
let skew = 0;
vi.spyOn(Date, "now").mockImplementation(() => realNow() + skew);
const pause = () => {
  skew += 1000;
};

beforeEach(() => {
  window.localStorage.clear();
  fake.providers = [];
  fake.status.mockResolvedValue({ pending_first_run: true, gateway_ids: [], plugins: BUNDLE });
  fake.ensurePopularPlugins.mockResolvedValue(undefined);
  fake.welcomeAfterSetup.mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("GatewaySetupNotice", () => {
  it("opens the setup on a new install, holds the tours, and can't be closed", async () => {
    render(<GatewaySetupNotice />);
    expect(await screen.findByRole("dialog", { name: "Welcome to UEFN Ducky" })).toBeTruthy();
    expect(fake.reportFirstRunSetup).toHaveBeenCalledWith(true);
    expect(screen.queryByRole("button", { name: "Close setup" })).toBeNull();
  });

  it("opens again after a restart part way through, even with the gateways already in", async () => {
    fake.status.mockResolvedValue({
      pending_first_run: true,
      gateway_ids: ["openai"],
      plugins: BUNDLE.map((p) => ({ ...p, installed: true })),
    });
    render(<GatewaySetupNotice />);
    expect(await screen.findByRole("dialog", { name: "Welcome to UEFN Ducky" })).toBeTruthy();
  });

  it("lets an existing install through: corner card, no setup, no hold; reopened, it still can't close", async () => {
    fake.status.mockResolvedValue({ pending_first_run: false, gateway_ids: [], plugins: BUNDLE });
    render(<GatewaySetupNotice />);
    expect(await screen.findByText("Finish setting up Ducky")).toBeTruthy();
    expect(fake.reportFirstRunSetup).toHaveBeenCalledWith(false);
    fireEvent.click(screen.getByRole("button", { name: "Continue setup" }));
    expect(screen.getByRole("dialog", { name: "Starter plugins" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Close setup" })).toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "Starter plugins" })).toBeTruthy();
  });

  it("installs the bundle and shows each plugin's progress", async () => {
    render(<GatewaySetupNotice />);
    fireEvent.click(await screen.findByRole("button", { name: /Get started/ }));
    pause();
    fireEvent.click(screen.getByRole("button", { name: /Install 2 plugins/ }));
    expect(fake.ensurePopularPlugins).toHaveBeenCalledWith(true);
    act(() => {
      fake.push.listener?.({ type: "starter_plugins_progress", slug: "openai", label: "OpenAI", setup_phase: "installing" });
    });
    expect(document.querySelector(".setup-wizard-plugin.is-installing")?.textContent).toContain("OpenAI");
    act(() => {
      fake.push.listener?.({ type: "starter_plugins_progress", slug: "openai", label: "OpenAI", setup_phase: "installed" });
      fake.push.listener?.({ type: "starter_plugins_progress", slug: "uefn", label: "UEFN", setup_phase: "error", detail: "Offline" });
    });
    expect(document.querySelector(".setup-wizard-plugin.is-installed")?.textContent).toContain("OpenAI");
    expect(screen.getByText("Offline")).toBeTruthy();
  });

  it("marks a plugin failed from the job's result even when its progress event was missed", async () => {
    fake.ensurePopularPlugins.mockResolvedValue({ ok: false, errors: [{ slug: "uefn", error: "Store is offline" }] });
    render(<GatewaySetupNotice />);
    fireEvent.click(await screen.findByRole("button", { name: /Get started/ }));
    pause();
    fireEvent.click(screen.getByRole("button", { name: /Install 2 plugins/ }));
    expect(await screen.findByText("Store is offline")).toBeTruthy();
    expect(document.querySelector(".setup-wizard-plugin.is-error")?.textContent).toContain("UEFN");
    expect(screen.getByRole("button", { name: /Try again/ })).toBeTruthy();
  });

  it("Finish asks for a 0–5 star rating, then hands over to the welcome ducky", async () => {
    let welcomed: () => void = () => {};
    fake.welcomeAfterSetup.mockReturnValue(new Promise<void>((r) => (welcomed = r)));
    render(<GatewaySetupNotice />);
    fireEvent.click(await screen.findByRole("button", { name: /Get started/ }));
    fake.status.mockResolvedValue({
      pending_first_run: true,
      gateway_ids: ["openai"],
      plugins: BUNDLE.map((p) => ({ ...p, installed: true })),
    });
    act(() => {
      fake.push.listener?.({ type: "uefn_plugins_changed" });
    });
    const next = await screen.findByRole("button", { name: /Next/ });
    pause();
    fireEvent.click(next);
    expect(screen.getByRole("dialog", { name: "Connect your AI" })).toBeTruthy();
    pause();
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    expect(screen.getByRole("dialog", { name: "Your defaults" })).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Discord" }));
    pause();
    fireEvent.click(screen.getByRole("button", { name: /Finish/ }));
    expect(fake.markStarterPluginToursCompleted).toHaveBeenCalled();
    expect(fake.markTourCompleted).toHaveBeenCalledWith("settings.store");
    expect(fake.markTourCompleted).toHaveBeenCalledWith("llms.setup");
    // The rating comes first: no close, the welcome waits for Submit.
    expect(screen.getByRole("dialog", { name: "How was getting started?" })).toBeTruthy();
    expect(fake.welcomeAfterSetup).not.toHaveBeenCalled();
    expect(fake.reportFirstRunSetup).toHaveBeenLastCalledWith(true);
    fireEvent.click(screen.getByRole("radio", { name: "4 stars" }));
    expect(screen.getByText("4 of 5 · Great")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fake.welcomeAfterSetup).toHaveBeenCalledOnce();
    // The app's own tours keep waiting until the welcome has run.
    expect(fake.reportFirstRunSetup).toHaveBeenLastCalledWith(true);
    expect(screen.queryByText("Finish setting up Ducky")).toBeNull();
    await act(async () => welcomed());
    await waitFor(() => expect(fake.reportFirstRunSetup).toHaveBeenLastCalledWith(false));
  });
});

describe("hiding the Set up Ducky pill", () => {
  it("the X hides the corner reminder for good, and Settings keeps it until setup is done", async () => {
    fake.status.mockResolvedValue({ pending_first_run: false, gateway_ids: [], plugins: BUNDLE });
    const { unmount } = render(
      <>
        <GatewaySetupNotice />
        <SetupReminder />
      </>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Minimize" }));
    fireEvent.click(screen.getByRole("button", { name: "Hide setup reminder" }));
    expect(screen.queryByRole("button", { name: "Set up Ducky" })).toBeNull();
    expect(screen.queryByText(/Setup installs it, then helps you connect it/)).toBeNull();
    // Settings' copy is still there and opens the setup.
    fireEvent.click(screen.getByRole("button", { name: "Continue setup" }));
    expect(screen.getByRole("dialog", { name: "Starter plugins" })).toBeTruthy();
    unmount();

    render(<GatewaySetupNotice />);
    await waitFor(() => expect(fake.status).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("Finish setting up Ducky")).toBeNull(); // still hidden after a restart
  });

  it("Settings shows nothing once a gateway is installed", async () => {
    fake.status.mockResolvedValue({ pending_first_run: false, gateway_ids: ["openai"], plugins: BUNDLE });
    render(
      <>
        <GatewaySetupNotice />
        <SetupReminder />
      </>,
    );
    await waitFor(() => expect(fake.status).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.queryByText("Finish setting up Ducky")).toBeNull();
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
      { slug: "a", label: "A", group: "ai" as const, installed: false },
      { slug: "b", label: "B", group: "uefn" as const, installed: false },
      { slug: "c", label: "C", group: "uefn" as const, installed: true },
    ];
    const next = mergeRows({ a: { phase: "installing" }, b: { phase: "error", detail: "x" } }, plugins);
    expect(next).toEqual({ a: { phase: "installing" }, b: { phase: "error", detail: "x" }, c: { phase: "installed" } });
  });
});
