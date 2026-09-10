import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./usePanelApi", () => ({
  getApi: vi.fn(),
}));
vi.mock("./usePanelPushBus", () => ({
  installPanelPushBus: vi.fn(),
  subscribePanelPush: vi.fn(() => () => {}),
}));
vi.mock("./bridgeJobAsync", () => ({
  runBridgeJob: vi.fn(),
}));

import { getApi } from "./usePanelApi";
import { runBridgeJob } from "./bridgeJobAsync";
import {
  clearModelsCatalog,
  getCachedModels,
  invalidateModelsCatalog,
  isModelsCatalogReady,
  loadModelsCatalog,
} from "./modelsCatalogCache";

describe("modelsCatalogCache", () => {
  beforeEach(() => {
    clearModelsCatalog();
    vi.mocked(getApi).mockReset();
    vi.mocked(runBridgeJob).mockReset();
  });

  it("keeps catalog ready across invalidate (stale-while-revalidate)", async () => {
    vi.mocked(getApi).mockReturnValue({
      get_key_status: async () => ({ openai: true }),
      get_settings: async () => ({ default_model: "", agent_model: "" }),
      get_models: async () => [
        {
          provider: "openai",
          id: "gpt-4o",
          name: "GPT-4o",
          supports_vision: true,
          supports_tools: true,
          supports_web_search: false,
          context_limit: 128000,
          price_in: null,
          price_out: null,
          is_local: false,
        },
      ],
    } as never);

    await loadModelsCatalog();
    expect(isModelsCatalogReady()).toBe(true);
    expect(getCachedModels()?.length).toBe(1);

    invalidateModelsCatalog();
    expect(isModelsCatalogReady()).toBe(true);
    expect(getCachedModels()?.[0]?.id).toBe("gpt-4o");
  });

  it("uses get_models_catalog and never runBridgeJob, even on force", async () => {
    vi.mocked(getApi).mockReturnValue({
      get_models_catalog: async () => ({
        models: [
          {
            provider: "OpenAI",
            provider_key: "openai",
            id: "gpt-4o",
            name: "GPT-4o",
            supports_vision: true,
            supports_tools: true,
            supports_web_search: false,
            context_limit: 128000,
            price_in: null,
            price_out: null,
            is_local: false,
          },
        ],
        default_model: "openai:gpt-4o",
        agent_model: "",
      }),
    } as never);

    await loadModelsCatalog({ force: true });
    expect(runBridgeJob).not.toHaveBeenCalled();
    expect(getCachedModels()?.[0]?.id).toBe("gpt-4o");
    expect(getCachedModels()?.[0]?.providerKey).toBe("openai");
  });
});
