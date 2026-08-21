import { describe, expect, it } from "vitest";
import { SETTINGS_CORE_TOUR, SETTINGS_STORE_TOUR, LLMS_SETUP_TOUR } from "./builtinTours";

describe("SETTINGS_CORE_TOUR", () => {
  it("spotlights General cards, Duckies drill, ducky skill HOW TO USE, MCP, demo plan, and Memory one-liner", () => {
    const targets = SETTINGS_CORE_TOUR.steps.map((s) => s.target);
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
      "settings.llms.section.memory",
    ]) {
      expect(targets, `missing target ${id}`).toContain(id);
    }
    const memoryBodies = SETTINGS_CORE_TOUR.steps
      .filter((s) => s.target === "settings.content" || s.target === "settings.llms.section.memory")
      .map((s) => s.body);
    expect(memoryBodies.some((b) => /Memory will be managed here/i.test(b))).toBe(true);
    expect(targets).not.toContain("settings.memory.section.entries");
    expect(targets).not.toContain("settings.memory.section.context");
  });
});

describe("first-run Store → LLM setup", () => {
  it("Store tour installs starter gateways then chains to LLM keys", () => {
    expect(SETTINGS_STORE_TOUR.onCompleteStart).toBe("llms.setup");
    const bodies = SETTINGS_STORE_TOUR.steps.map((s) => s.body).join(" ");
    expect(bodies).toMatch(/Anthropic/);
    expect(bodies).toMatch(/Cursor/);
    expect(bodies).toMatch(/OpenAI/);
  });

  it("LLM setup walkthrough opens each starter provider and covers connection", () => {
    const targets = LLMS_SETUP_TOUR.steps.map((s) => s.target);
    for (const id of [
      "settings.tab.llms",
      "settings.llms.provider.anthropic",
      "settings.llms.provider.cursor",
      "settings.llms.provider.openai",
      "settings.llms.provider.key",
      "settings.llms.provider.save",
      "settings.llms.provider.ide",
      "settings.llms.provider.ide.apply",
      "settings.llms.provider.agent",
      "settings.llms.provider.agent.detect",
      "settings.llms.provider.plugin",
      "settings.llms.back",
    ]) {
      expect(targets, `missing target ${id}`).toContain(id);
    }
    const bodies = LLMS_SETUP_TOUR.steps.map((s) => s.body).join(" ");
    expect(bodies).toMatch(/IDE \/ MCP/);
    expect(bodies).toMatch(/Claude Code/);
    expect(bodies).toMatch(/Codex/);
    expect(bodies).toMatch(/cache/i);
    for (const id of [
      "settings.tab.llms",
      "settings.llms.provider.anthropic",
      "settings.llms.provider.cursor",
      "settings.llms.provider.openai",
      "settings.llms.back",
    ]) {
      const step = LLMS_SETUP_TOUR.steps.find((s) => s.target === id);
      expect(step?.advance, `${id} should require a press`).toBe("require_click");
    }
  });
});
