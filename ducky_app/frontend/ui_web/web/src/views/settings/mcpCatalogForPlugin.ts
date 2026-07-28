import type { McpCatalogDto, McpCategoryDto, McpPluginDto, McpToolDto } from "../../types/panel";

const BUILTIN_DUCKY = "builtin_ducky";
const DUCKY_PREFIXES = ["ducky_", "workspace_"] as const;

function isDuckyBuiltinTool(name: string): boolean {
  return DUCKY_PREFIXES.some((p) => name.startsWith(p));
}

function toolBelongsToPlugin(plugin: McpPluginDto, tool: McpToolDto): boolean {
  if (plugin.kind === "builtin") {
    if (plugin.id === BUILTIN_DUCKY) {
      return !tool.is_plugin && isDuckyBuiltinTool(tool.name);
    }
    return false;
  }
  if (plugin.kind === "uefn_plugin") {
    const names = plugin.tool_names ?? [];
    if (names.length === 0) return false;
    return names.includes(tool.name);
  }
  const prefix = (plugin.tool_prefix || plugin.id).trim();
  if (!prefix) return false;
  return Boolean(tool.is_plugin && tool.name.startsWith(`${prefix}__`));
}

function regroupByCategory(tools: McpToolDto[]): McpCategoryDto[] {
  const buckets = new Map<string, McpCategoryDto>();
  for (const tool of tools) {
    const existing = buckets.get(tool.category_id);
    if (existing) {
      existing.tools.push(tool);
    } else {
      buckets.set(tool.category_id, {
        id: tool.category_id,
        label: tool.category_label,
        tools: [tool],
      });
    }
  }
  return Array.from(buckets.values());
}

export function mcpCatalogForPlugin(plugin: McpPluginDto, catalog: McpCatalogDto): McpCategoryDto[] {
  const tools = catalog.tools.filter((t) => toolBelongsToPlugin(plugin, t));
  const categories = regroupByCategory(tools);

  const order = new Map(catalog.categories.map((c, i) => [c.id, i]));
  categories.sort((a, b) => {
    const ai = order.get(a.id) ?? Number.MAX_SAFE_INTEGER;
    const bi = order.get(b.id) ?? Number.MAX_SAFE_INTEGER;
    if (ai !== bi) return ai - bi;
    return a.label.localeCompare(b.label);
  });

  return categories;
}

export function filterMcpCategories(categories: McpCategoryDto[], query: string): McpCategoryDto[] {
  const q = query.trim().toLowerCase();
  if (!q) return categories;
  return categories
    .map((cat) => ({
      ...cat,
      tools: cat.tools.filter(
        (t) =>
          t.name.toLowerCase().includes(q) ||
          t.description.toLowerCase().includes(q) ||
          t.category_label.toLowerCase().includes(q),
      ),
    }))
    .filter((cat) => cat.tools.length > 0);
}
