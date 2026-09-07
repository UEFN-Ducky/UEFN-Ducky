import { describe, expect, it } from "vitest";
import {
  inferLlmsWalkthroughProviderId,
  isLlmsProviderRowTarget,
  pluginManifestToTour,
} from "./pluginWalkthroughs";

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
