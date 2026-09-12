import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { clearCachedCodingAgents, setCachedCodingAgents } from "../../hooks/codingAgentsCache";
import type { CodingAgentDto } from "../../types/panel";
import {
  codingAgentFromModel,
  formToConfig,
  formToProfilePatch,
  isModelGateError,
  modelShowsThinkingEffort,
  serializeDuckyForm,
  validateModelSelection,
  type DuckyProfileFormState,
} from "./duckyProfileForm";

function baseForm(overrides: Partial<DuckyProfileFormState> = {}): DuckyProfileFormState {
  return {
    name: "Tester",
    duckyStyle: "artist",
    personality: "",
    whenToUse: "",
    disabledPacks: [],
    enabledSubskills: {},
    disabledToolIds: [],
    model: "",
    ttsVoice: "",
    ttsSpeed: 0,
    thinkingEffort: "off",
    ...overrides,
  };
}

/** What the host reports when Cursor is installed and running. */
const agents: CodingAgentDto[] = [
  { id: "cursor", label: "Cursor", enabled: true, available: true, status: "ok" },
];

describe("duckyProfileForm model selection", () => {
  // `formToConfig` and friends read the live list from the cache, since a plain
  // form helper has no component to thread it through.
  beforeEach(() => setCachedCodingAgents(agents));
  afterEach(() => clearCachedCodingAgents());

  it("allows empty model (global Default Model applies)", () => {
    expect(validateModelSelection("")).toBeNull();
    expect(validateModelSelection("   ")).toBeNull();
  });

  it("rejects agent-only names and invalid qualified ids", () => {
    expect(validateModelSelection("cursor")).toMatch(/exact model/i);
    expect(validateModelSelection("cursor:composer-2.5")).toBeNull();
    expect(validateModelSelection("cursor:default")).toBeNull();
  });

  it("says nothing about an agent name it has never heard of", () => {
    clearCachedCodingAgents();
    // With no live list, "cursor" is just a word: warning that it is an agent
    // name would be a guess, and blocking the save on a guess is worse.
    expect(validateModelSelection("cursor")).toBeNull();
  });

  it("flags model messages for the pick gate", () => {
    expect(isModelGateError("No model selected. Pick a model on this Ducky.")).toBe(true);
    expect(isModelGateError("Set a Default Model in Settings → LLMs.")).toBe(true);
    expect(isModelGateError("Network error")).toBe(false);
  });

  it("derives coding_agent from qualified model", () => {
    expect(codingAgentFromModel("cursor:composer-2.5")).toBe("cursor");
    expect(codingAgentFromModel("anthropic:claude-sonnet-4-20250514")).toBe("ducky");
    expect(codingAgentFromModel("")).toBe("ducky");
  });

  it("takes an explicit list over the cached one", () => {
    clearCachedCodingAgents();
    expect(codingAgentFromModel("cursor:composer-2.5")).toBe("ducky");
    expect(codingAgentFromModel("cursor:composer-2.5", agents)).toBe("cursor");
  });

  it("classifies a contrib coding agent before detect cache lands", () => {
    clearCachedCodingAgents();
    expect(codingAgentFromModel("claude_code:sonnet-5", [], ["claude_code"])).toBe("claude_code");
  });

  it("persists a single favorite_models entry when set", () => {
    const form = baseForm({ model: "cursor:vanished" });
    const patch = formToProfilePatch(form);
    expect(patch.favorite_models).toEqual(["cursor:vanished"]);
    const config = formToConfig(form);
    expect(config.favorite_models).toEqual(["cursor:vanished"]);
    expect(config.coding_agent).toBe("cursor");
  });

  it("stores profile_id on chat config when creating from a library agent", () => {
    const form = baseForm({ name: "Star" });
    const config = formToConfig(form, "New ducky", "star-uuid");
    expect(config.profile_id).toBe("star-uuid");
    expect(config.ducky_name).toBe("Star");
    expect(formToConfig(form).profile_id).toBeUndefined();
  });

  it("persists an empty favorite_models list when unset", () => {
    const form = baseForm({ model: "" });
    expect(formToProfilePatch(form).favorite_models).toEqual([]);
    expect(formToConfig(form).favorite_models).toEqual([]);
    expect(formToConfig(form).coding_agent).toBe("ducky");
  });

  it("serializeDuckyForm is order-stable for dirty checks", () => {
    const a = baseForm({ disabledPacks: ["b", "a"], disabledToolIds: ["z", "y"] });
    const b = baseForm({ disabledPacks: ["a", "b"], disabledToolIds: ["y", "z"] });
    expect(serializeDuckyForm(a)).toBe(serializeDuckyForm(b));
    expect(serializeDuckyForm(a)).not.toBe(serializeDuckyForm(baseForm({ name: "Other" })));
  });

  it("formToConfig persists skill deny-lists and tool deny-lists", () => {
    const form = baseForm({
      disabledPacks: ["animation"],
      enabledSubskills: { verse: ["digests"] },
      disabledToolIds: ["blender"],
    });
    const config = formToConfig(form);
    expect(config.disabled_packs).toEqual(["animation"]);
    expect(config.enabled_subskills).toEqual({ verse: ["digests"] });
    expect(config.disabled_tool_ids).toEqual(["blender"]);
  });

  it("formToConfig writes thinking_effort for Anthropic and Astra models", () => {
    const form = baseForm({
      model: "anthropic:claude-sonnet-4-20250514",
      thinkingEffort: "high",
    });
    expect(formToConfig(form).thinking_effort).toBe("high");
    expect(formToConfig(baseForm({ model: "openai:gpt-6-astra", thinkingEffort: "high" })).thinking_effort).toBe(
      "high",
    );
    expect(formToConfig(baseForm({ model: "openai:gpt-4o", thinkingEffort: "high" })).thinking_effort).toBe(
      "off",
    );
  });

  it("shows the effort picker for Claude and Astra", () => {
    expect(modelShowsThinkingEffort("anthropic:claude-sonnet-4")).toBe(true);
    expect(modelShowsThinkingEffort("openai:gpt-6-astra")).toBe(true);
    expect(modelShowsThinkingEffort("openai:gpt-4o")).toBe(false);
  });

  it("prefers a per-model catalog flag over the name heuristic", () => {
    const catalog = [
      {
        provider: "OpenAI",
        providerKey: "openai",
        id: "gpt-4o",
        name: "GPT-4o",
        supportsVision: true,
        supportsTools: true,
        supportsWebSearch: false,
        contextLimit: 128000,
        priceIn: null,
        priceOut: null,
        isLocal: false,
        supportsThinkingEffort: true,
      },
      {
        provider: "Anthropic",
        providerKey: "anthropic",
        id: "claude-sonnet-4",
        name: "Claude Sonnet 4",
        supportsVision: true,
        supportsTools: true,
        supportsWebSearch: false,
        contextLimit: 200000,
        priceIn: null,
        priceOut: null,
        isLocal: false,
        supportsThinkingEffort: false,
      },
      {
        provider: "Cursor",
        providerKey: "cursor",
        id: "composer-2.5",
        name: "Composer 2.5",
        supportsVision: true,
        supportsTools: true,
        supportsWebSearch: false,
        contextLimit: 0,
        priceIn: null,
        priceOut: null,
        isLocal: false,
        supportsThinkingEffort: false,
      },
    ];
    expect(modelShowsThinkingEffort("openai:gpt-4o", [], [], catalog)).toBe(true);
    expect(modelShowsThinkingEffort("anthropic:claude-sonnet-4", [], [], catalog)).toBe(false);
    expect(
      modelShowsThinkingEffort(
        "cursor:composer-2.5",
        [{ id: "cursor", shows_thinking_effort: true }],
        [],
        catalog,
      ),
    ).toBe(false);
  });
});
