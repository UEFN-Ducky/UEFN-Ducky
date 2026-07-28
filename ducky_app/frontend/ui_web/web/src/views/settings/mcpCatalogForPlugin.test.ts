import { describe, expect, it } from "vitest";
import { mcpCatalogForPlugin } from "./mcpCatalogForPlugin";
import type { McpCatalogDto, McpPluginDto, McpToolDto } from "../../types/panel";

function tool(
  name: string,
  opts: Partial<McpToolDto> & { category_id?: string; category_label?: string } = {},
): McpToolDto {
  const category_id = opts.category_id ?? "misc";
  return {
    name,
    description: opts.description ?? name,
    category_id,
    category_label: opts.category_label ?? category_id,
    in_agent: opts.in_agent ?? true,
    in_plan: opts.in_plan ?? false,
    agent_excluded: opts.agent_excluded ?? false,
    destructive: opts.destructive ?? false,
    host_only: opts.host_only ?? false,
    is_plugin: opts.is_plugin ?? false,
    parameters: opts.parameters ?? [],
  };
}

const catalog: McpCatalogDto = {
  total: 5,
  agent_tools: 5,
  plan_tools: 0,
  categories: [
    { id: "scene", label: "Scene", tools: [] },
    { id: "workspace", label: "Workspace", tools: [] },
    { id: "panel", label: "Panel", tools: [] },
    { id: "plugin", label: "Plugin", tools: [] },
  ],
  tools: [
    tool("spawn_actor", { category_id: "scene", category_label: "Scene" }),
    tool("workspace_read_file", { category_id: "workspace", category_label: "Workspace" }),
    tool("ducky_get_status", { category_id: "panel", category_label: "Panel" }),
    tool("hook_probe_ping", { category_id: "plugin", category_label: "Plugin" }),
    tool("example__list", {
      category_id: "plugin",
      category_label: "Plugin",
      is_plugin: true,
    }),
  ],
};

describe("mcpCatalogForPlugin", () => {
  it("groups ducky_* / workspace_* under builtin_ducky only", () => {
    const ducky: McpPluginDto = {
      id: "builtin_ducky",
      label: "Ducky",
      description: "",
      version: 1,
      kind: "builtin",
      transport: "builtin",
      tags: [],
      enabled: true,
      default_enabled: true,
      requirements_note: "",
      setup_steps: [],
      tool_prefix: "",
      intents: [],
      path: "",
    };

    const duckyNames = mcpCatalogForPlugin(ducky, catalog).flatMap((c) => c.tools.map((t) => t.name));

    expect(duckyNames).toContain("workspace_read_file");
    expect(duckyNames).toContain("ducky_get_status");
    expect(duckyNames).not.toContain("spawn_actor");
  });

  it("legacy builtin_uefn row matches nothing", () => {
    const uefn: McpPluginDto = {
      id: "builtin_uefn",
      label: "UEFN",
      description: "",
      version: 1,
      kind: "builtin",
      transport: "builtin",
      tags: [],
      enabled: true,
      default_enabled: true,
      requirements_note: "",
      setup_steps: [],
      tool_prefix: "",
      intents: [],
      path: "",
    };
    expect(mcpCatalogForPlugin(uefn, catalog)).toEqual([]);
  });

  it("filters uefn_plugin rows by tool_names", () => {
    const plugin: McpPluginDto = {
      id: "hookprobe",
      label: "Hook Probe",
      description: "",
      version: 1,
      kind: "uefn_plugin",
      transport: "builtin",
      tags: ["app-plugin"],
      enabled: true,
      default_enabled: false,
      requirements_note: "",
      setup_steps: [],
      tool_prefix: "",
      tool_names: ["hook_probe_ping"],
      intents: [],
      path: "",
    };
    const names = mcpCatalogForPlugin(plugin, catalog).flatMap((c) => c.tools.map((t) => t.name));
    expect(names).toEqual(["hook_probe_ping"]);
  });
});
