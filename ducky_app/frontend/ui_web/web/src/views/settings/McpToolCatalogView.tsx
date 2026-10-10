import { useEffect, useMemo, useState } from "react";
import type { McpCategoryDto, McpDiagnosticsDto, McpToolDto } from "../../types/panel";
import { getApi } from "../../hooks/usePanelApi";

const BADGE_META: Record<string, { label: string; color: string; title: string }> = {
  agent: {
    label: "Agent",
    color: "var(--green)",
    title: "Eligible for the in-panel Agent catalog. This does not prove runtime model exposure.",
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
    title: "Excluded from the in-panel Agent catalog. IDE model exposure is not observed here.",
  },
};

/** The same sanitized report returned by ducky_get_tools(diagnostics=true). */
export function McpDiagnosticsView({ serverId, refreshKey }: { serverId: string; refreshKey?: unknown }) {
  const [mode, setMode] = useState<McpDiagnosticsDto["mode"]>("agent");
  const [report, setReport] = useState<McpDiagnosticsDto | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    setReport(null);
    setUnavailable(false);
    const api = getApi();
    if (!api?.get_mcp_diagnostics) {
      setUnavailable(true);
      return;
    }
    void api.get_mcp_diagnostics(serverId, mode).then((value) => {
      if (active) setReport(value);
    }).catch(() => { if (active) setUnavailable(true); });
    return () => { active = false; };
  }, [serverId, mode, refreshKey, refresh]);
  const count = (value: number | null) => value === null ? "Unknown" : value;
  const stage = (value: boolean | null) => value === null ? "Unknown" : value ? "Yes" : "No";
  return (
    <section aria-label="MCP diagnostics" className="mcp-plugin-setup">
      <h3>Connection and mode diagnostics</h3>
      <label>Policy preview mode{" "}
        <select aria-label="Policy preview mode" value={mode} onChange={(e) => setMode(e.target.value as McpDiagnosticsDto["mode"])}>
          <option value="agent">Agent</option><option value="ask">Ask</option><option value="plan">Plan</option>
        </select>
      </label>{" "}
      <button type="button" onClick={() => setRefresh((n) => n + 1)}>Refresh observations</button>
      <p>This preview does not change the chat mode or connect to a server.</p>
      {unavailable ? <p role="status">Diagnostics unavailable. Runtime status is unknown.</p> : !report ? <p role="status">Reading cached observations…</p> : <>
        <p>Correlation ID: <code>{report.correlation_id}</code></p>
        <p>{report.scope}</p>
        <p>Stage totals — Catalog tools: {count(report.stage_counts.catalog_tools)} · Started servers: {count(report.stage_counts.started_servers)} · Connected servers: {count(report.stage_counts.connected_servers)} · Model-exposed tools: {count(report.stage_counts.model_exposed_tools)}</p>
        {report.observation_error ? <p role="status">Observations unavailable. Runtime status is unknown.</p> : null}
        {report.servers.map((row) => <div key={row.server_id}>
          <p><strong>{row.server_id}</strong> — {row.status}</p>
          <p>Catalog tools: {count(row.counts.catalog)} · Policy-blocked: {count(row.counts.policy_blocked)} · Unexposed by local filter: {count(row.counts.unexposed_by_filter)}</p>
          <p>Started: {stage(row.stages.started)} · Connected: {stage(row.stages.connected)} · Model-exposed: {stage(row.stages.model_exposed)}</p>
          <p>Recent error: {row.recent_error === "inventory_refresh_failed" ? "Inventory refresh failed" : row.recent_error === "http_connection_failed" ? "HTTP connection failed" : "Unobserved"}</p>
          <p>{row.guidance}</p>
          {row.tools.some((tool) => tool.status !== "unknown") ? <details><summary>Tool exclusions and mode reasons</summary><ul>
            {row.tools.filter((tool) => tool.status !== "unknown").map((tool) => <li key={tool.name}><code>{tool.name}</code>: {tool.status}{tool.mode_reason ? ` — ${tool.mode_reason}` : ""}</li>)}
          </ul></details> : null}
          {row.tools_truncated ? <p>Showing up to 50 tool details. Counts include all observed tools.</p> : null}
        </div>)}
        <p>{report.stage_note}</p><p>{report.policy_guidance}</p>
      </>}
    </section>
  );
}

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
