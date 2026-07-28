import { describe, expect, it } from "vitest";
import { isStandaloneToolCard, resolveToolCategory } from "./toolCategories";

describe("resolveToolCategory", () => {
  it("maps ask-user tools", () => {
    expect(resolveToolCategory("ducky_ask_user").id).toBe("ask_user");
    expect(resolveToolCategory("ducky_ask_user").Body).toBeTruthy();
    expect(resolveToolCategory("ducky_ask_user").label?.("ducky_ask_user")).toBe(
      "Clarify with user",
    );
    expect(isStandaloneToolCard("ducky_ask_user")).toBe(true);
  });

  it("maps walkthrough tools", () => {
    expect(resolveToolCategory("ducky_walkthrough_run").id).toBe("walkthrough");
    expect(resolveToolCategory("ducky_walkthrough_run").Body).toBeTruthy();
    expect(resolveToolCategory("ducky_walkthrough_run").label?.("ducky_walkthrough_run")).toBe(
      "UI Tutorial",
    );
  });

  it("maps skill tools", () => {
    expect(resolveToolCategory("uefn_skill").id).toBe("skill");
    expect(resolveToolCategory("skill_read_subskill").id).toBe("skill");
    expect(resolveToolCategory("Skill").id).toBe("skill");
  });

  it("maps terminal tools", () => {
    expect(resolveToolCategory("Bash").id).toBe("terminal");
    expect(resolveToolCategory("PowerShell").id).toBe("terminal");
    expect(resolveToolCategory("ducky_terminal_run").id).toBe("terminal");
  });

  it("only file writes and ask-user stay standalone (reads/bash fold into accordion)", () => {
    expect(isStandaloneToolCard("workspace_write_file")).toBe(true);
    expect(isStandaloneToolCard("Write")).toBe(true);
    expect(isStandaloneToolCard("Edit")).toBe(true);
    expect(isStandaloneToolCard("ducky_ask_user")).toBe(true);
    expect(isStandaloneToolCard("workspace_read_file")).toBe(false);
    expect(isStandaloneToolCard("Read")).toBe(false);
    expect(isStandaloneToolCard("Bash")).toBe(false);
    expect(isStandaloneToolCard("Grep")).toBe(false);
    expect(isStandaloneToolCard("workspace_list_verse_errors")).toBe(false);
  });

  it("maps screenshot tools", () => {
    expect(resolveToolCategory("take_high_res_screenshot").id).toBe("screenshot");
    expect(resolveToolCategory("preview_asset").id).toBe("screenshot");
  });

  it("maps python tools", () => {
    expect(resolveToolCategory("execute_python").id).toBe("python");
    expect(resolveToolCategory("ai_generate_python").id).toBe("python");
  });

  it("maps search before verse for search_verse_digest", () => {
    expect(resolveToolCategory("search_verse_digest").id).toBe("search");
    expect(resolveToolCategory("search_assets").id).toBe("search");
    expect(resolveToolCategory("Grep").id).toBe("search");
    expect(resolveToolCategory("Glob").id).toBe("search");
    expect(resolveToolCategory("workspace_list_dir").id).toBe("search");
  });

  it("maps verse tools", () => {
    expect(resolveToolCategory("workspace_compile_verse").id).toBe("verse");
    expect(resolveToolCategory("workspace_list_verse_errors").id).toBe("verse");
    expect(resolveToolCategory("inspect_verse_device").id).toBe("verse");
  });

  it("maps landscape tools", () => {
    expect(resolveToolCategory("get_ground_z").id).toBe("landscape");
    expect(resolveToolCategory("foliage_scatter").id).toBe("landscape");
  });

  it("falls back to generic for unknown tools", () => {
    expect(resolveToolCategory("spawn_actor").id).toBe("generic");
    expect(resolveToolCategory("save_current_level").id).toBe("generic");
    expect(resolveToolCategory("ping").id).toBe("generic");
  });

  it("attaches bespoke bodies where expected", () => {
    expect(resolveToolCategory("Bash").Body).toBeTruthy();
    expect(resolveToolCategory("uefn_skill").Body).toBeTruthy();
    expect(resolveToolCategory("take_high_res_screenshot").Body).toBeTruthy();
    expect(resolveToolCategory("search_assets").Body).toBeTruthy();
    expect(resolveToolCategory("execute_python").Body).toBeUndefined();
    expect(resolveToolCategory("workspace_compile_verse").Body).toBeUndefined();
    expect(resolveToolCategory("spawn_actor").Body).toBeUndefined();
  });

  it("labels ToolSearch as Tool Registry Search", () => {
    const cat = resolveToolCategory("ToolSearch");
    expect(cat.id).toBe("search");
    expect(cat.label?.("ToolSearch")).toBe("Tool Registry Search");
  });
});
