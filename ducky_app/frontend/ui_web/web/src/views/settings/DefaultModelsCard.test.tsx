// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const fake = vi.hoisted(() => ({
  api: {
    get_settings: vi.fn(),
    save_agent_settings: vi.fn(),
    get_memory_settings: vi.fn(),
    set_memory_settings: vi.fn(),
    list_workflow_nodes: vi.fn(),
    list_media_makers: vi.fn(),
    duckyos_store_catalog: vi.fn(),
  },
  saveVoice: vi.fn(),
}));

vi.mock("../../hooks/usePanelApi", () => ({ getApi: () => fake.api, isRemote: () => false }));
vi.mock("../../hooks/onApiReady", () => ({ onApiReady: (cb: (api: unknown) => void) => (cb(fake.api), () => {}) }));
vi.mock("../../hooks/usePanelPushBus", () => ({ installPanelPushBus: () => {}, subscribePanelPush: () => () => {} }));
vi.mock("../../hooks/bridgeJobAsync", () => ({ runBridgeJob: vi.fn() }));
vi.mock("../../hooks/storeCatalogCache", () => ({ fetchStoreCatalog: () => Promise.resolve({ items: [] }) }));
vi.mock("../../voice/voiceSettings", () => ({
  loadVoiceSettings: () => Promise.resolve({ liveModel: "openai:gpt-mini", summaryModel: "" }),
  saveVoiceSettings: fake.saveVoice,
}));
vi.mock("../../components/ducky/DuckyModelPicker", () => ({
  DuckyModelPicker: ({ model, onChange, placeholder }: { model: string; onChange: (m: string) => void; placeholder?: string }) => (
    <button type="button" onClick={() => onChange("anthropic:claude-haiku")}>
      {model || placeholder}
    </button>
  ),
}));

const { DefaultModelsCard } = await import("./DefaultModelsCard");

const backend = (plugin: string, plugin_id: string) => ({ id: plugin_id, label: plugin, plugin, plugin_id, credits: 5, cost: "~5 credits", available: true });

beforeEach(() => {
  fake.api.get_settings.mockResolvedValue({ default_model: "anthropic:claude-opus", default_video_provider: "" });
  fake.api.save_agent_settings.mockResolvedValue(undefined);
  fake.api.get_memory_settings.mockResolvedValue({ memory_summary_model: "" });
  fake.api.set_memory_settings.mockResolvedValue({});
  fake.api.list_workflow_nodes.mockResolvedValue({
    nodes: [
      { type: "image.generate", backends: [backend("Ducky AI", "account")] },
      { type: "mesh.generate", backends: [backend("Meshy", "meshy")] },
    ],
  });
  fake.api.list_media_makers.mockResolvedValue({ ok: true, makers: { video: [], music: [] } });
  fake.saveVoice.mockResolvedValue({});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const row = (name: string) => screen.getByRole("listitem", { name });

it("lists every default in the one card: a model per job, then who makes pictures and 3D models", async () => {
  render(<DefaultModelsCard />);
  expect(screen.getByRole("heading", { name: "Default models" })).toBeTruthy();
  await waitFor(() => expect(within(row("Chats and duckies")).getByRole("button").textContent).toBe("anthropic:claude-opus"));
  await waitFor(() => expect(within(row("Live voice")).getByRole("button").textContent).toBe("openai:gpt-mini"));
  expect(within(row("Spoken summaries")).getByRole("button").textContent).toBe("Chats model");
  expect(within(row("Memory summaries")).getByRole("button").textContent).toBe("Spoken summaries model");
  expect(within(row("Pictures")).getByRole("button", { name: "Pictures" })).toBeTruthy();
  expect(within(row("3D models")).getByRole("button", { name: "3D models" })).toBeTruthy();
  // No plugin here makes videos or music: no made-up rows.
  expect(screen.queryByRole("listitem", { name: "Videos" })).toBeNull();
  expect(screen.queryByRole("listitem", { name: "Music" })).toBeNull();
});

it("saves each job's model where that job keeps it", async () => {
  render(<DefaultModelsCard />);
  await waitFor(() => expect(within(row("Chats and duckies")).getByRole("button").textContent).toBe("anthropic:claude-opus"));
  fireEvent.click(within(row("Chats and duckies")).getByRole("button"));
  expect(fake.api.save_agent_settings).toHaveBeenCalledWith({ default_model: "anthropic:claude-haiku" });
  fireEvent.click(within(row("Live voice")).getByRole("button"));
  expect(fake.saveVoice).toHaveBeenCalledWith({ liveModel: "anthropic:claude-haiku" });
  fireEvent.click(within(row("Spoken summaries")).getByRole("button"));
  expect(fake.saveVoice).toHaveBeenCalledWith({ summaryModel: "anthropic:claude-haiku" });
  fireEvent.click(within(row("Memory summaries")).getByRole("button"));
  expect(fake.api.set_memory_settings).toHaveBeenCalledWith({ memory_summary_model: "anthropic:claude-haiku" });
  expect(await screen.findByText(/Saved\./)).toBeTruthy();
});

it("shows Videos only when a plugin here makes them, and saves the pick", async () => {
  fake.api.list_media_makers.mockResolvedValue({
    ok: true,
    makers: { video: [{ id: "higgsfield", label: "Higgsfield", ready: true }, { id: "other", label: "Other", ready: false }], music: [] },
  });
  render(<DefaultModelsCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Videos" }));
  expect(screen.getByRole("radio", { name: /Other/ })).toBeTruthy();
  expect(screen.getByText("Not set up")).toBeTruthy(); // installed, its key not saved yet
  fireEvent.click(screen.getByRole("radio", { name: /Higgsfield/ }));
  expect(fake.api.save_agent_settings).toHaveBeenCalledWith({ default_video_provider: "higgsfield" });
  expect(screen.queryByRole("listitem", { name: "Music" })).toBeNull();
});
