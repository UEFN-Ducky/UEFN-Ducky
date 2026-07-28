import { useEffect, useMemo, useState } from "react";
import type { McpCategoryDto, McpToolDto } from "../../types/panel";

const BADGE_META: Record<string, { label: string; color: string; title: string }> = {
  agent: {
    label: "Agent",
    color: "var(--green)",
    title: "Exposed to the in-panel Ducky agent (chat).",
  },
  plan: {
    label: "Plan",
    color: "var(--blue)",
    title: "Usable in Plan mode (read/discover + plan trees). Mutating tools stay Agent-only.",
  },
  plugin: {
    label: "Plugin",
    color: "var(--blue)",
    title: "Provided by a nested MCP plugin (prefix__tool).",
  },
  host: {
    label: "Host",
    color: "var(--amber)",
    title: "Runs in the Ducky app (disk/panel). Does not need the UEFN editor listener.",
  },
  destructive: {
    label: "Destructive",
    color: "var(--red)",
    title: "Can delete or overwrite data — use carefully.",
  },
  mcp_only: {
    label: "MCP only",
    color: "var(--muted)",
    title: "On the MCP server for IDE agents, but not given to the in-panel Ducky agent.",
  },
};

const BADGE_MODIFIER: Record<string, string> = {
  "var(--green)": "skills-mcp-badge--green",
  "var(--blue)": "skills-mcp-badge--blue",
  "var(--amber)": "skills-mcp-badge--amber",
  "var(--red)": "skills-mcp-badge--red",
  "var(--muted)": "skills-mcp-badge--muted",
};

function Badge({ kind }: { kind: keyof typeof BADGE_META }) {
  const meta = BADGE_META[kind];
  const modifier = BADGE_MODIFIER[meta.color] ?? "skills-mcp-badge--muted";
  return (
    <span className={`skills-mcp-badge ${modifier}`} title={meta.title}>
      {meta.label}
    </span>
  );
}

export function flattenMcpTools(categories: McpCategoryDto[]): McpToolDto[] {
  return categories.flatMap((c) => c.tools);
}

export function ToolInspector({ tool }: { tool: McpToolDto | null }) {
  if (!tool) {
    return <p className="catalog-slide-empty">Select a tool to inspect it.</p>;
  }
  return (
    <div className="skills-mcp-tool-inspector">
      <div className="skills-mcp-tool-inspector-header">
        <h3 className="skills-mcp-tool-inspector-name">{tool.name}</h3>
        <div className="skills-mcp-tool-card-badges">
          {tool.in_agent ? <Badge kind="agent" /> : null}
          {tool.in_plan ? <Badge kind="plan" /> : null}
          {tool.is_plugin ? <Badge kind="plugin" /> : null}
          {tool.host_only ? <Badge kind="host" /> : null}
          {tool.destructive ? <Badge kind="destructive" /> : null}
          {tool.agent_excluded ? <Badge kind="mcp_only" /> : null}
        </div>
      </div>
      {tool.description ? (
        <p className="skills-mcp-tool-inspector-desc">{tool.description}</p>
      ) : null}
      {tool.parameters.length > 0 ? (
        <div className="skills-mcp-tool-inspector-params">
          <div className="skills-mcp-tool-card-params-label">Parameters</div>
          <dl className="skills-mcp-tool-inspector-params-list">
            {tool.parameters.map((p) => (
              <div key={p.name} className="skills-mcp-tool-inspector-param">
                <dt>
                  <span className="skills-mcp-tool-card-param-name">
                    {p.name}
                    {p.required ? <span className="skills-mcp-tool-card-param-required">*</span> : null}
                  </span>
                  <span className="skills-mcp-tool-card-param-type">{p.type}</span>
                </dt>
                {p.description ? <dd className="skills-mcp-tool-card-param-desc">{p.description}</dd> : null}
              </div>
            ))}
          </dl>
        </div>
      ) : (
        <div className="skills-mcp-tool-card-no-params">No parameters</div>
      )}
    </div>
  );
}

type McpToolSplitProps = {
  categories: McpCategoryDto[];
  /** Unfiltered catalog — used so a selected tool still inspects when filtered out of the list. */
  allCategories?: McpCategoryDto[];
  query?: string;
  onQueryChange?: (query: string) => void;
  selectedToolName: string | null;
  onSelectTool: (name: string) => void;
  totalCount?: number;
  loading?: boolean;
  emptyMessage?: string;
};

export function McpToolSplitView({
  categories,
  allCategories,
  query,
  onQueryChange,
  selectedToolName,
  onSelectTool,
  totalCount,
  loading = false,
  emptyMessage = "No tools match your filter.",
}: McpToolSplitProps) {
  const tools = useMemo(() => flattenMcpTools(categories), [categories]);
  const allTools = useMemo(
    () => flattenMcpTools(allCategories ?? categories),
    [allCategories, categories],
  );
  const selected = allTools.find((t) => t.name === selectedToolName) ?? null;
  const showSearch = onQueryChange !== undefined;
  const filtering = Boolean(showSearch && query?.trim() && totalCount !== undefined);
  const statsLabel =
    totalCount === undefined
      ? null
      : filtering
        ? `${tools.length} of ${totalCount}`
        : `${totalCount} tools`;

  const [openIds, setOpenIds] = useState<Set<string>>(() => new Set());

  // Keep selected tool's category open; when filtering, open every match group.
  useEffect(() => {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (filtering) {
        for (const cat of categories) next.add(cat.id);
      }
      if (selectedToolName) {
        const cat = (allCategories ?? categories).find((c) =>
          c.tools.some((t) => t.name === selectedToolName),
        );
        if (cat) next.add(cat.id);
      } else if (!filtering && categories.length > 0 && next.size === 0) {
        next.add(categories[0].id);
      }
      return next;
    });
  }, [categories, allCategories, selectedToolName, filtering]);

  const toggleCategory = (id: string) => {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <>
      {showSearch ? (
        <div className="skills-mcp-mcp-toolbar">
          {statsLabel ? <span className="skills-mcp-mcp-stats">{statsLabel}</span> : <span className="skills-mcp-mcp-stats" />}
          <input
            type="search"
            value={query ?? ""}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="Filter tools…"
            className="skills-mcp-mcp-search"
          />
        </div>
      ) : null}
      {loading ? (
        <div className="skills-mcp-mcp-empty">Loading MCP catalog…</div>
      ) : tools.length === 0 ? (
        <div className="skills-mcp-mcp-empty">{emptyMessage}</div>
      ) : (
        <div className="catalog-slide-split">
          <div className="catalog-slide-split-left" role="listbox" aria-label="Tools by category">
            {categories.map((cat) => {
              const open = openIds.has(cat.id) || filtering;
              return (
                <div key={cat.id} className="catalog-slide-tool-cat">
                  <button
                    type="button"
                    className="catalog-slide-tool-cat-toggle"
                    aria-expanded={open}
                    onClick={() => toggleCategory(cat.id)}
                  >
                    <span className={`catalog-slide-tool-cat-chevron${open ? " is-open" : ""}`} aria-hidden>
                      ▾
                    </span>
                    <span className="catalog-slide-tool-cat-title">{cat.label}</span>
                    <span className="catalog-slide-tool-cat-count">{cat.tools.length}</span>
                  </button>
                  {open
                    ? cat.tools.map((tool) => (
                        <button
                          key={tool.name}
                          type="button"
                          role="option"
                          aria-selected={selectedToolName === tool.name}
                          className={`catalog-slide-tool-btn${selectedToolName === tool.name ? " is-active" : ""}`}
                          onClick={() => onSelectTool(tool.name)}
                        >
                          <span className="catalog-slide-tool-btn-name">{tool.name}</span>
                          {tool.description ? (
                            <span className="catalog-slide-tool-btn-desc">{tool.description}</span>
                          ) : null}
                        </button>
                      ))
                    : null}
                </div>
              );
            })}
          </div>
          <div className="catalog-slide-split-right">
            <ToolInspector tool={selected} />
            {selected && filtering && !tools.some((t) => t.name === selected.name) ? (
              <p className="skills-mcp-tool-inspector-filter-hint">Not in current filter results.</p>
            ) : null}
          </div>
        </div>
      )}
    </>
  );
}
