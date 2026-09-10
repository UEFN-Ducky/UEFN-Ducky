import { afterEach, describe, expect, it } from "vitest";
import {
  APP_SHELL_TOUR,
  CHAT_COMPOSER_TOUR,
  SETTINGS_GENERAL_TOUR,
  SETTINGS_DUCKIES_TOUR,
  SETTINGS_PLANS_TOUR,
  SETTINGS_LLMS_TOUR,
  SETTINGS_STORE_TOUR,
  LLMS_SETUP_TOUR,
} from "./builtinTours";
import { _resetWalkthroughServiceForTests, HOST_TOUR_CATALOG_IDS, registerTour } from "./WalkthroughService";

describe("CHAT_COMPOSER_TOUR", () => {
  it("spotlights the composer and each toolbar control", () => {
    const targets = CHAT_COMPOSER_TOUR.steps.map((s) => s.target);
    expect(targets).toEqual([
      "chat.composer",
      "chat.composer.input",
      "chat.composer.mode",
      "chat.composer.usage",
      "chat.composer.model",
      "chat.composer.changes",
      "chat.composer.snip",
      "chat.composer.mic",
      "chat.composer.live",
      "chat.composer.send",
    ]);
    expect(CHAT_COMPOSER_TOUR.steps[0]?.advance).toBe("next");
    for (const step of CHAT_COMPOSER_TOUR.steps.slice(1)) {
      expect(step.advance, `${step.target} should require a press`).toBe("require_click");
    }
  });
});

describe("split settings tours", () => {
  it("spotlights General cards, Duckies drill, ducky skill HOW TO USE, MCP, demo plan, and Memory one-liner", () => {
    const targets = [
      ...SETTINGS_GENERAL_TOUR.steps,
      ...SETTINGS_DUCKIES_TOUR.steps,
      ...SETTINGS_PLANS_TOUR.steps,
      ...SETTINGS_LLMS_TOUR.steps,
    ].map((s) => s.target);
    for (const id of [
      "settings.general.app",
      "settings.general.project_files",
      "settings.general.add_to_uefn",
      "settings.general.app_data",
      "settings.duckies.row.first",
      "settings.duckies.section.skills",
      "settings.duckies.section.mcps",
      "settings.duckies.back",
      "settings.skills.row.ducky",
      "settings.skills.back",
      "settings.mcp.list",
      "settings.mcp.add",
      "settings.plans.row.demo-getting-started",
      "settings.llms.providers",
      "settings.llms.section.memory",
    ]) {
      expect(targets, `missing target ${id}`).toContain(id);
    }
    const memoryBodies = SETTINGS_LLMS_TOUR.steps
      .filter((s) => s.target === "settings.content" || s.target === "settings.llms.section.memory")
      .map((s) => s.body);
    expect(memoryBodies.some((b) => /Memory will be managed here/i.test(b))).toBe(true);
    expect(targets).not.toContain("settings.memory.section.entries");
    expect(targets).not.toContain("settings.memory.section.context");
  });
});

describe("first-run Store → LLM setup", () => {
  it("Welcome chains to Store, not the old giant Settings tour", () => {
    expect(APP_SHELL_TOUR.onCompleteStart).toBe("settings.store");
    expect(HOST_TOUR_CATALOG_IDS).toContain("chat.composer");
    expect(HOST_TOUR_CATALOG_IDS).not.toContain("settings.core");
  });

  it("Store tour installs starter gateways then chains to LLM keys", () => {
    expect(SETTINGS_STORE_TOUR.onCompleteStart).toBe("llms.setup");
    const bodies = SETTINGS_STORE_TOUR.steps.map((s) => s.body).join(" ");
    expect(bodies).toMatch(/Anthropic/);
    expect(bodies).toMatch(/Cursor/);
    expect(bodies).toMatch(/OpenAI/);
  });

  it("LLM setup intro opens the providers table; plugin tours fill each gateway", () => {
    const targets = LLMS_SETUP_TOUR.steps.map((s) => s.target);
    expect(targets).toEqual(["settings.tab.llms", "settings.llms.providers"]);
    expect(LLMS_SETUP_TOUR.steps[0]?.advance).toBe("require_click");
    expect(LLMS_SETUP_TOUR.steps.map((s) => s.body).join(" ")).toMatch(/Press it and the page slides open/i);
  });
});

describe("LLM tours use enabled plugin gateway walkthroughs", () => {
  afterEach(() => {
    _resetWalkthroughServiceForTests();
  });

  function registerGateway(id: string, extras: string[] = []): void {
    registerTour({
      id: `plugin.${id}`,
      title: `Set up ${id}`,
      steps: [
        {
          target: `settings.llms.provider.${id}`,
          title: `Open ${id}`,
          body: `Press the ${id} row.`,
          advance: "require_click",
        },
        { target: "settings.llms.provider.key", title: "Key", body: "Paste a key.", advance: "next" },
        {
          target: "settings.llms.provider.save",
          title: "Test & Save",
          body: "Press Test & Save.",
          advance: "require_click",
        },
        ...extras.map((target) => ({
          target,
          title: target,
          body: target,
          advance: "next" as const,
        })),
        { target: "settings.llms.back", title: "Back", body: "Press Back.", advance: "require_click" as const },
      ],
    });
  }

  it("settings.llms walks every section then one enabled gateway", () => {
    registerGateway("openai", ["settings.llms.provider.agent"]);
    registerGateway("anthropic", [
      "settings.llms.provider.ide",
      "settings.llms.provider.agent",
      "settings.llms.provider.plugin",
    ]);
    const steps = SETTINGS_LLMS_TOUR.resolveSteps?.() ?? [];
    const targets = steps.map((s) => s.target);
    expect(targets).toContain("settings.llms.section.skills");
    expect(targets).toContain("settings.llms.section.mcps");
    expect(targets).toContain("settings.llms.section.memory");
    expect(targets).toContain("settings.llms.provider.anthropic");
    expect(targets).not.toContain("settings.llms.provider.openai");
    expect(steps.filter((s) => s.target === "settings.llms.provider.anthropic")[0]?.advance).toBe(
      "require_click",
    );
  });

  it("llms.setup walks every enabled gateway tour", () => {
    registerGateway("openai", ["settings.llms.provider.agent", "settings.llms.provider.plugin"]);
    registerGateway("cursor", ["settings.llms.provider.ide", "settings.llms.provider.agent"]);
    const steps = LLMS_SETUP_TOUR.resolveSteps?.() ?? [];
    const targets = steps.map((s) => s.target);
    expect(targets[0]).toBe("settings.tab.llms");
    expect(targets).toContain("settings.llms.provider.cursor");
    expect(targets).toContain("settings.llms.provider.openai");
    expect(targets).toContain("settings.llms.provider.ide");
    expect(targets).toContain("settings.llms.provider.plugin");
    expect(targets.filter((t) => t === "settings.llms.back")).toHaveLength(2);
    for (const id of ["settings.llms.provider.cursor", "settings.llms.provider.openai", "settings.llms.back"]) {
      const step = steps.find((s) => s.target === id);
      expect(step?.advance, `${id} should require a press`).toBe("require_click");
    }
  });
});
