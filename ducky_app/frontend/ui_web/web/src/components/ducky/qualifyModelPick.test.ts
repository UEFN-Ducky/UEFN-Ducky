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

  it("falls back to gemini when the catalog has no match (Anthropic/OpenAI are plugin-only)", () => {
    expect(qualifyModelPick("ducky", "gemini-2.0-flash")).toBe("gemini:gemini-2.0-flash");
  });
});
