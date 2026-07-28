import { describe, expect, it } from "vitest";
import { SETTINGS_CORE_TOUR } from "./builtinTours";

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
