import { afterEach, describe, expect, it } from "vitest";
import {
  expandGatewayManifest,
  inferLlmsWalkthroughProviderId,
  isLlmsProviderRowTarget,
  listEnabledGatewayTours,
  pluginManifestToTour,
} from "./pluginWalkthroughs";
import { _resetWalkthroughServiceForTests, registerTour } from "./WalkthroughService";

describe("isLlmsProviderRowTarget", () => {
  it("matches the table row, not the slide fields", () => {
    expect(isLlmsProviderRowTarget("settings.llms.provider.openai")).toBe(true);
    expect(isLlmsProviderRowTarget("settings.llms.provider.gemini")).toBe(true);
    expect(isLlmsProviderRowTarget("settings.llms.provider.key")).toBe(false);
    expect(isLlmsProviderRowTarget("settings.llms.provider.save")).toBe(false);
    expect(isLlmsProviderRowTarget("settings.llms.provider.agent")).toBe(false);
  });
});

describe("inferLlmsWalkthroughProviderId", () => {
  it("prefers the row target when plugin id differs (google → gemini)", () => {
    expect(
      inferLlmsWalkthroughProviderId({
        id: "google",
        steps: [{ target: "settings.llms.provider.gemini", title: "Google", body: "" }],
      }),
    ).toBe("gemini");
  });

  it("uses provider_id when set", () => {
    expect(
      inferLlmsWalkthroughProviderId({
        id: "google",
        provider_id: "gemini",
        steps: [{ target: "settings.llms.provider.key", title: "Key", body: "" }],
      }),
    ).toBe("gemini");
  });
});

describe("pluginManifestToTour", () => {
  it("spotlights the provider row, key, Test, and coding agent", () => {
    const tour = pluginManifestToTour({
      id: "openai",
      settings_tab: "LLMs",
      steps: [
        {
          target: "settings.llms.provider.openai",
          title: "Open OpenAI",
          body: "Press the OpenAI row.",
          advance: "require_click",
        },
        { target: "settings.llms.provider.key", title: "API key or Codex", body: "Paste a key or use Codex." },
        {
          target: "settings.llms.provider.save",
          title: "Test & Save",
          body: "Press Test & Save.",
          advance: "require_click",
        },
        { target: "settings.llms.provider.agent", title: "Codex", body: "Codex is the coding agent." },
      ],
    });
    expect(tour?.id).toBe("plugin.openai");
    expect(tour?.steps.map((s) => s.target)).toEqual([
      "settings.llms.provider.openai",
      "settings.llms.provider.key",
      "settings.llms.provider.save",
      "settings.llms.provider.agent",
    ]);
    expect(tour?.steps[0]?.advance).toBe("require_click");
    expect(tour?.steps[2]?.advance).toBe("require_click");
    expect(tour?.steps.every((s) => typeof s.onEnter === "function")).toBe(true);
  });
});

describe("expandGatewayManifest", () => {
  const row = {
    id: "openai",
    plugin_id: "openai",
    title: "Set up OpenAI",
    settings_tab: "LLMs",
    steps: [
      {
        target: "settings.llms.provider.openai",
        title: "Open OpenAI",
        body: "Press the OpenAI row.",
        advance: "require_click" as const,
      },
      { target: "settings.llms.provider.key", title: "API key or Codex", body: "Paste a key or use Codex." },
    ],
  };

  it("fills save, agent, plugin, and back from contributions", () => {
    const expanded = expandGatewayManifest(row, {
      llm_coding_agents: [{ plugin_id: "openai" }],
      settings_sections: [
        {
          tab: "LLMs",
          plugin_id: "openai",
          title: "OpenAI prompt caching",
          description: "Cache markers reuse the system prefix.",
        },
      ],
    });
    expect(expanded.steps.map((s) => s.target)).toEqual([
      "settings.llms.provider.openai",
      "settings.llms.provider.key",
      "settings.llms.provider.save",
      "settings.llms.provider.agent",
      "settings.llms.provider.agent.detect",
      "settings.llms.provider.plugin",
      "settings.llms.back",
    ]);
    expect(expanded.steps.find((s) => s.target === "settings.llms.provider.key")?.body).toMatch(/Codex/);
    expect(expanded.steps.find((s) => s.target === "settings.llms.provider.plugin")?.title).toBe(
      "OpenAI prompt caching",
    );
    expect(expanded.steps.find((s) => s.target === "settings.llms.back")?.advance).toBe("require_click");
  });

  it("keeps a plugin-authored login spotlight on the coding-agent row", () => {
    const expanded = expandGatewayManifest(
      {
        ...row,
        id: "anthropic",
        plugin_id: "anthropic",
        steps: [
          { ...row.steps[0], target: "settings.llms.provider.anthropic" },
          {
            target: "settings.llms.provider.agent.login",
            title: "Log in here",
            body: "Never paste a login code in chat.",
            advance: "require_click",
          },
        ],
      },
      { llm_coding_agents: [{ plugin_id: "anthropic" }] },
    );
    expect(expanded.steps.map((s) => s.target)).toContain("settings.llms.provider.agent.login");
    expect(expanded.steps.find((s) => s.target === "settings.llms.provider.agent.login")?.body).toMatch(
      /Never paste a login code in chat/,
    );
    expect(expanded.steps.find((s) => s.target === "settings.llms.provider.agent.login")?.advance).toBe(
      "require_click",
    );
  });

  it("adds IDE steps only when the plugin contributes a hookup", () => {
    const expanded = expandGatewayManifest(
      { ...row, id: "cursor", plugin_id: "cursor", steps: [{ ...row.steps[0], target: "settings.llms.provider.cursor" }] },
      { ide_hookups: [{ plugin_id: "cursor" }], llm_coding_agents: [{ plugin_id: "cursor" }] },
    );
    expect(expanded.steps.map((s) => s.target)).toEqual([
      "settings.llms.provider.cursor",
      "settings.llms.provider.key",
      "settings.llms.provider.save",
      "settings.llms.provider.ide",
      "settings.llms.provider.ide.apply",
      "settings.llms.provider.agent",
      "settings.llms.provider.agent.detect",
      "settings.llms.back",
    ]);
  });

  it("leaves non-gateway tours alone", () => {
    const raw = {
      id: "translation",
      steps: [{ target: "settings.translation.export", title: "Export", body: "Export PO files." }],
    };
    expect(expandGatewayManifest(raw)).toBe(raw);
  });
});

describe("listEnabledGatewayTours", () => {
  afterEach(() => {
    _resetWalkthroughServiceForTests();
  });

  it("returns registered plugin tours that click a provider row", () => {
    registerTour({
      id: "plugin.openai",
      steps: [
        { target: "settings.llms.provider.openai", title: "Open", body: "row", advance: "require_click" },
      ],
    });
    registerTour({
      id: "plugin.translation",
      steps: [{ target: "settings.translation.export", title: "Export", body: "po", advance: "next" }],
    });
    expect(listEnabledGatewayTours().map((t) => t.id)).toEqual(["plugin.openai"]);
  });
});
