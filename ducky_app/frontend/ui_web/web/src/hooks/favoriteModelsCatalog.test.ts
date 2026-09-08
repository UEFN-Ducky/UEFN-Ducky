import { describe, expect, it } from "vitest";
import {
  buildFavoritePickerCatalog,
  isLegacyAgentOnlyFavorite,
  parseFavoriteSelection,
  qualifyFavorite,
  selectionNeedsRepick,
} from "./favoriteModelsCatalog";
import type { CatalogModelRow } from "./modelsCatalogCache";
import type { CodingAgentDto } from "../types/panel";

const liveApi: CatalogModelRow[] = [
  {
    provider: "Anthropic",
    providerKey: "anthropic",
    id: "claude-sonnet-4-20250514",
    name: "Claude Sonnet 4",
    supportsVision: true,
    supportsTools: true,
    supportsWebSearch: false,
    contextLimit: 200000,
    priceIn: 3,
    priceOut: 15,
    isLocal: false,
  },
];

const agents: CodingAgentDto[] = [
  {
    id: "cursor",
    label: "Cursor",
    enabled: true,
    available: true,
    status: "ok",
    models: [
      { id: "composer-2.5", name: "Composer 2.5", provider: "Cursor" },
      { id: "default", name: "Auto", provider: "Cursor" },
    ],
  },
];

describe("favoriteModelsCatalog", () => {
  it("qualifies live API and coding-agent models without hardcoded baseline ids", () => {
    const catalog = buildFavoritePickerCatalog([], liveApi, agents);
    const ids = catalog.map((r) => r.id);
    expect(ids).toContain("anthropic:claude-sonnet-4-20250514");
    expect(ids).toContain("cursor:composer-2.5");
    // Legacy catalog aliases are normalized to the SDK invocation id.
    expect(ids).toContain("cursor:auto");
    // No legacy agent-only row unless it was saved.
    expect(ids).not.toContain("cursor");
  });

  it("keeps saved missing selections as synthetic unavailable rows", () => {
    const catalog = buildFavoritePickerCatalog(
      ["cursor:vanished-model", "cursor"],
      liveApi,
      agents,
    );
    const vanished = catalog.find((r) => r.id === "cursor:vanished-model");
    expect(vanished?.name).toContain("unavailable");
    const legacy = catalog.find((r) => r.id === "cursor");
    expect(legacy?.name).toMatch(/pick a model/i);
  });

  it("parses qualified selections and flags legacy agent-only values", () => {
    expect(qualifyFavorite("cursor", "composer-2.5")).toBe("cursor:composer-2.5");
    expect(parseFavoriteSelection("cursor:composer-2.5", agents)?.modelId).toBe("composer-2.5");
    // "default" means "let the agent choose" and only a coding agent has that idea.
    expect(parseFavoriteSelection("cursor:default", agents)?.modelId).toBe("auto");
    expect(parseFavoriteSelection("anthropic:default", agents)).toBeNull();
    expect(isLegacyAgentOnlyFavorite("cursor", agents)).toBe(true);
    expect(selectionNeedsRepick("cursor", agents)).toBe(true);
    expect(selectionNeedsRepick("cursor:composer-2.5", agents)).toBe(false);
  });

  it("knows nothing is a coding agent until the live list arrives", () => {
    // Before the host answers, "cursor:default" cannot be told from a provider
    // whose model is literally named default — so it is not accepted as one.
    expect(parseFavoriteSelection("cursor:default")).toBeNull();
    expect(parseFavoriteSelection("cursor:composer-2.5")?.modelId).toBe("composer-2.5");
    expect(isLegacyAgentOnlyFavorite("cursor")).toBe(false);
  });
});
