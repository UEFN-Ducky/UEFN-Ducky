import { describe, expect, it, vi } from "vitest";

vi.mock("../../hooks/modelsCatalogCache", () => ({
  getCachedModels: () => [
    {
      provider: "OpenAI",
      providerKey: "openai",
      id: "gpt-4o-mini",
      name: "GPT-4o mini",
      supportsVision: false,
      supportsTools: true,
      supportsWebSearch: false,
      contextLimit: 128000,
      priceIn: 0.15,
      priceOut: 0.6,
      isLocal: false,
    },
  ],
}));

import { qualifyModelPick } from "./DuckyModelPicker";

describe("qualifyModelPick", () => {
  it("qualifies coding-agent picks", () => {
    expect(qualifyModelPick("cursor", "composer-2.5")).toBe("cursor:composer-2.5");
    expect(qualifyModelPick("cursor", "default")).toBe("cursor:auto");
  });

  it("qualifies ducky API picks from the live catalog", () => {
    expect(qualifyModelPick("ducky", "gpt-4o-mini")).toBe("openai:gpt-4o-mini");
  });

  it("never guesses a provider for a model the catalog does not know", () => {
    // Returning "" means "use the Default Model". Inventing a provider would
    // store a selection that cannot resolve, and the UI only ever offers models
    // that are in the catalog, so this case does not arise from a real pick.
    expect(qualifyModelPick("ducky", "gemini-2.0-flash")).toBe("");
  });

  it("passes an already-qualified id through untouched", () => {
    expect(qualifyModelPick("ducky", "gemini:gemini-2.0-flash")).toBe("gemini:gemini-2.0-flash");
  });

  it("qualifies against the agent even when the model is not an API one", () => {
    // A coding agent's models are never in the API catalog; the agent id decides.
    expect(qualifyModelPick("claude-code", "opus")).toBe("claude_code:opus");
  });
});
