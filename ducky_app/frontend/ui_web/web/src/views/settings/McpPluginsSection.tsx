import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  AgentCatalogCreateModal,
  CatalogDetailHead,
  CatalogListRow,
  CatalogSlideShell,
  CatalogSourceBadge,
  useCatalogSlideNav,
  type CatalogBreadcrumb,
  type CatalogSource,
} from "../../components/catalog-slide";
import { ChoiceDropdown } from "../../components/ChoiceDropdown";
import { Modal, ModalActions } from "../../components/Modal";
import { useConfirmModal } from "../../contexts/ConfirmModalContext";
import { getApi } from "../../hooks/usePanelApi";
import { onApiReady } from "../../hooks/onApiReady";
import { Icons } from "../../icons/Icons";
import type { SettingsNavLocation } from "../../navigation/settingsHistory";
import {
  useApplySettingsDrill,
  useRecordSettingsLocation,
  useSettingsHistoryBack,
} from "../../navigation/useSettingsHistory";
import type { McpCatalogDto, McpPluginDto, McpPluginTestResultDto } from "../../types/panel";
import { filterMcpCategories, mcpCatalogForPlugin } from "./mcpCatalogForPlugin";
import { McpToolSplitView } from "./McpToolCatalogView";
import { targetRef } from "../../ui-targets/registry";

const TRANSPORT_OPTIONS = [
  { value: "stdio", label: "stdio", hint: "Local subprocess" },
  { value: "http", label: "http", hint: "Streamable HTTP" },
  { value: "sse", label: "sse", hint: "Server-sent events" },
] as const;

/** Cursor-shaped sample for the mcp.json editor (stdio + http). */
const MCP_JSON_EXAMPLE = `{
  "mcpServers": {
    "demo": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-everything"],
      "label": "Demo"
    },
    "remote": {
      "type": "http",
      "url": "https://host/mcp",
      "headers": {
        "Authorization": "Bearer \${SECRET:MY_TOKEN}"
      },
      "label": "Remote"
    }
  }
}
`;

type TransportValue = (typeof TRANSPORT_OPTIONS)[number]["value"];

function BoxesIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
      <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
      <line x1="12" y1="22.08" x2="12" y2="12" />
    </svg>
  );
}

function mcpServerSource(server: McpPluginDto): CatalogSource {
  if (server.kind === "builtin") return "builtin";
  if (server.kind === "uefn_plugin" || server.kind === "catalog") return "plugin";
  return "custom";
}

function canDeleteServer(server: McpPluginDto): boolean {
  return server.kind !== "catalog" && server.kind !== "builtin" && server.kind !== "uefn_plugin";
}

function TransportDropdown({
  value,
  onChange,
}: {
  value: TransportValue;
  onChange: (next: TransportValue) => void;
}) {
  return (
    <ChoiceDropdown
      className="mcp-create-select"
      mode="radio"
      aria-label="Transport"
      minWidth={280}
      value={value}
      options={TRANSPORT_OPTIONS.map((o) => ({ value: o.value, label: o.label, hint: o.hint }))}
      onChange={(next) => onChange(next as TransportValue)}
    />
  );
}

function ListSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mcp-catalog-section">
      <h3 className="mcp-catalog-section-title">{title}</h3>
      <ul className="catalog-slide-list">{children}</ul>
    </div>
  );
}

export function McpPluginsSection() {
  const { confirm } = useConfirmModal();
  const [plugins, setPlugins] = useState<McpPluginDto[]>([]);
  const [catalog, setCatalog] = useState<McpCatalogDto | null>(null);
  const [loadError, setLoadError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [testResults, setTestResults] = useState<Record<string, McpPluginTestResultDto>>({});
  const [showCreate, setShowCreate] = useState(false);
  const [listQuery, setListQuery] = useState("");
  const [toolQuery, setToolQuery] = useState("");
  const [createTransport, setCreateTransport] = useState<TransportValue>("stdio");
  const [createLabel, setCreateLabel] = useState("");
  const [createCommand, setCreateCommand] = useState("");
  const [createArgs, setCreateArgs] = useState("");
  const [createUrl, setCreateUrl] = useState("");
  const [createHeaders, setCreateHeaders] = useState("");
  const [showConfig, setShowConfig] = useState(false);
  const [configText, setConfigText] = useState("");
  const [configPath, setConfigPath] = useState("");
  const [configError, setConfigError] = useState("");
  const [configSaving, setConfigSaving] = useState(false);

  const {
    selectedKey,
    setSelectedKey,
    focusId,
    setFocusId,
    detailOpen,
    detailRendered,
    openDetail,
    closeDetail,
  } = useCatalogSlideNav();

  const nestedServers = useMemo(
    () => plugins.filter((p) => p.kind !== "builtin" && p.kind !== "uefn_plugin"),
    [plugins],
  );
  const builtinGroups = useMemo(() => plugins.filter((p) => p.kind === "builtin"), [plugins]);
  const desktopPluginTools = useMemo(() => plugins.filter((p) => p.kind === "uefn_plugin"), [plugins]);

  const selectedServer = useMemo(
    () => (selectedKey ? plugins.find((p) => p.id === selectedKey) ?? null : null),
    [plugins, selectedKey],
  );

  const filterServers = useCallback(
    (rows: McpPluginDto[]) => {
      const q = listQuery.trim().toLowerCase();
      if (!q) return rows;
      return rows.filter(
        (s) =>
          s.label.toLowerCase().includes(q) ||
          s.id.toLowerCase().includes(q) ||
          (s.description || "").toLowerCase().includes(q),
      );
    },
    [listQuery],
  );

  const refreshPlugins = useCallback(() => {
    const api = getApi();
    if (!api?.list_mcp_plugins) return;
    void api
      .list_mcp_plugins()
      .then((res) => setPlugins(res.plugins ?? []))
      .catch((err: unknown) => setLoadError(err instanceof Error ? err.message : String(err)));
  }, []);

  const refreshCatalog = useCallback(() => {
    const api = getApi();
    if (!api?.get_mcp_tools_catalog && !(api as { bridge_job_start?: unknown } | null)?.bridge_job_start) {
      return;
    }
    void import("../../hooks/bridgeJobAsync")
      .then(({ runBridgeJob }) => runBridgeJob<McpCatalogDto>("get_mcp_tools_catalog", [], 120_000))
      .then(setCatalog)
      .catch(() => setCatalog(null));
  }, []);

  const loadConfigEditor = useCallback(async () => {
    const api = getApi();
    if (!api?.get_mcp_config) return;
    setConfigError("");
    try {
      const res = await api.get_mcp_config();
      if (!res.ok) {
        setConfigError(res.error || "Failed to load mcp.json");
        return;
      }
      setConfigText(res.text || '{\n  "mcpServers": {}\n}\n');
      setConfigPath(res.path || "");
    } catch (err: unknown) {
      setConfigError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(
    () =>
      onApiReady(() => {
        refreshPlugins();
        refreshCatalog();
      }),
    [refreshPlugins, refreshCatalog],
  );

  useEffect(() => {
    if (selectedServer && !catalog) refreshCatalog();
  }, [selectedServer, catalog, refreshCatalog]);

  const resetCreateForm = useCallback(() => {
    setCreateTransport("stdio");
    setCreateLabel("");
    setCreateCommand("");
    setCreateArgs("");
    setCreateUrl("");
    setCreateHeaders("");
  }, []);

  const closeCreate = useCallback(() => {
    setShowCreate(false);
    resetCreateForm();
  }, [resetCreateForm]);

  const toggleEnabled = async (server: McpPluginDto) => {
    const api = getApi();
    if (!api?.set_mcp_plugin_enabled) return;
    if (!server.enabled && server.enable_blocked_by_port) {
      const peers = (server.port_conflict_with || []).join(", ") || "another server";
      setLoadError(
        `Cannot enable "${server.label}" — port ${server.http_bind || "?"} is already used by ${peers}. Disable that server first, or change one URL.`,
      );
      return;
    }
    setBusyId(server.id);
    try {
      const result = await api.set_mcp_plugin_enabled(server.id, !server.enabled);
      if (result && typeof result === "object" && "ok" in result && result.ok === false) {
        const err =
          (result as { error?: string; needs_trust?: boolean }).error ||
          ((result as { needs_trust?: boolean }).needs_trust
            ? "Confirm trust for this desktop plugin in Settings → Store first."
            : "Failed to update server");
        setLoadError(err);
      } else {
        setLoadError("");
      }
      refreshPlugins();
      refreshCatalog();
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId("");
    }
  };

  const testServer = async (server: McpPluginDto) => {
    const api = getApi();
    if (!api?.test_mcp_plugin) return;
    setBusyId(server.id);
    try {
      const { runBridgeJob } = await import("../../hooks/bridgeJobAsync");
      const result = await runBridgeJob<McpPluginTestResultDto>("test_mcp_plugin", [server.id], 120_000);
      setTestResults((prev) => ({ ...prev, [server.id]: result }));
      refreshCatalog();
    } catch (err: unknown) {
      setTestResults((prev) => ({
        ...prev,
        [server.id]: { ok: false, error: err instanceof Error ? err.message : String(err) },
      }));
    } finally {
      setBusyId("");
    }
  };

  const openFolder = () => {
    void getApi()?.open_mcp_plugins_folder?.();
  };

  const openConfigEditor = () => {
    setShowConfig(true);
    void loadConfigEditor();
  };

  const saveConfig = async () => {
    const api = getApi();
    if (!api?.set_mcp_config) return;
    setConfigSaving(true);
    setConfigError("");
    try {
      const res = await api.set_mcp_config(configText);
      if (!res.ok) {
        setConfigError(res.error || "Failed to save mcp.json");
        return;
      }
      setShowConfig(false);
      refreshPlugins();
      refreshCatalog();
    } catch (err: unknown) {
      setConfigError(err instanceof Error ? err.message : String(err));
    } finally {
      setConfigSaving(false);
    }
  };

  const createServer = async (serverId: string, description: string) => {
    const api = getApi();
    if (!api?.create_mcp_plugin) return;
    const isHttp = createTransport === "http" || createTransport === "sse";
    const args = createArgs
      .split(/\s+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const headers: Record<string, string> = {};
    for (const line of createHeaders.split("\n")) {
      const idx = line.indexOf(":");
      if (idx <= 0) continue;
      const key = line.slice(0, idx).trim();
      const val = line.slice(idx + 1).trim();
      if (key) headers[key] = val;
    }
    await api.create_mcp_plugin(
      serverId.trim(),
      (createLabel.trim() || serverId.trim()),
      description.trim(),
      isHttp ? "" : createCommand.trim(),
      isHttp ? [] : args,
      undefined,
      "",
      undefined,
      createTransport,
      isHttp ? createUrl.trim() : "",
      isHttp ? headers : undefined,
    );
    refreshPlugins();
  };

  const deleteServer = async (server: McpPluginDto) => {
    if (!canDeleteServer(server)) return;
    const api = getApi();
    if (!api?.delete_mcp_plugin) return;
    const ok = await confirm({
      title: "Delete MCP server?",
      message: `Delete custom server "${server.label}" from mcp.json?`,
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    setBusyId(server.id);
    try {
      await api.delete_mcp_plugin(server.id);
      if (selectedKey === server.id) closeDetail();
      refreshPlugins();
      refreshCatalog();
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId("");
    }
  };

  const handleOpenServer = (server: McpPluginDto) => {
    setToolQuery("");
    openDetail(server.id);
  };

  const pluginCategories = useMemo(() => {
    if (!selectedServer || !catalog) return [];
    return mcpCatalogForPlugin(selectedServer, catalog);
  }, [selectedServer, catalog]);

  const filteredToolCategories = useMemo(
    () => filterMcpCategories(pluginCategories, toolQuery),
    [pluginCategories, toolQuery],
  );

  const toolTotalCount = pluginCategories.reduce((n, c) => n + c.tools.length, 0);

  // History records server + tool levels (Skills parity) so in-pane Back steps one level.
  const mcpsNavLoc = useMemo<SettingsNavLocation>(() => {
    if (!selectedKey) {
      return {
        kind: "settings",
        tab: "LLMs",
        sectionTab: "mcps",
        name: "LLMs · MCPs",
      };
    }
    const title = focusId || selectedServer?.label || selectedKey;
    return {
      kind: "settings",
      tab: "LLMs",
      sectionTab: "mcps",
      drill: { type: "mcps", pluginId: selectedKey, toolName: focusId },
      name: title,
    };
  }, [selectedKey, selectedServer?.label, focusId]);
  useRecordSettingsLocation(mcpsNavLoc);

  const applyMcpsDrill = useCallback(
    (loc: SettingsNavLocation) => {
      if (loc.sectionTab && loc.sectionTab !== "mcps") return;
      const drill = loc.drill?.type === "mcps" ? loc.drill : null;
      const pluginId = drill?.pluginId ?? null;
      if (!pluginId) {
        closeDetail();
        return;
      }
      if (!plugins.some((p) => p.id === pluginId)) {
        closeDetail();
        return;
      }
      setSelectedKey(pluginId);
      setFocusId(drill?.toolName || null);
      setToolQuery("");
    },
    [plugins, closeDetail, setSelectedKey, setFocusId],
  );
  useApplySettingsDrill("LLMs", applyMcpsDrill);

  const historyCloseDetail = useSettingsHistoryBack(closeDetail);

  // History records server + tool — one back always goes to the previous level.
  const handleDetailBack = useCallback(() => {
    historyCloseDetail();
  }, [historyCloseDetail]);

  const goToMcpsList = useCallback(() => {
    // From a tool, pop tool→server then server→list so "MCPs" lands on the catalog.
    if (focusId) historyCloseDetail();
    historyCloseDetail();
  }, [focusId, historyCloseDetail]);

  const breadcrumbs = useMemo((): CatalogBreadcrumb[] => {
    if (!selectedServer) return [];
    return [
      {
        id: "mcps",
        label: "MCPs",
        onClick: goToMcpsList,
      },
      {
        id: selectedServer.id,
        label: selectedServer.label,
        current: !focusId,
        onClick: focusId ? historyCloseDetail : undefined,
      },
      ...(focusId
        ? [
            {
              id: focusId,
              label: focusId,
              current: true,
            },
          ]
        : []),
    ];
  }, [selectedServer, focusId, historyCloseDetail, goToMcpsList]);

  const renderServerRow = (server: McpPluginDto) => {
    const busy = busyId === server.id;
    const toggleId = `mcp-server-enabled-${server.id}`;
    const enableBlocked = Boolean(server.enable_blocked_by_port);
    const portConflict = Boolean(server.port_conflict);
    return (
      <CatalogListRow
        key={server.id}
        title={server.label}
        source={mcpServerSource(server)}
        selected={selectedKey === server.id}
        disabled={busy}
        icon={<BoxesIcon />}
        meta={
          <>
            <code>{server.id}</code>
            {server.transport && server.transport !== "stdio" && server.transport !== "builtin" ? (
              <span>· {server.transport}</span>
            ) : null}
            {server.http_bind ? <span>· {server.http_bind}</span> : null}
            <span>· {server.enabled ? "Enabled" : "Disabled"}</span>
            {portConflict ? <span>· port conflict</span> : null}
            {enableBlocked ? <span>· port in use</span> : null}
          </>
        }
        overview={
          enableBlocked
            ? `Port ${server.http_bind} is used by ${(server.port_conflict_with || []).join(", ") || "another MCP"}. Disable that server before enabling this one.`
            : portConflict
              ? `Shares TCP port ${server.http_bind} with ${(server.port_conflict_with || []).join(", ")}. Only one HTTP/SSE MCP may own a port — disable extras.`
              : server.description?.trim() || undefined
        }
        onOpen={() => handleOpenServer(server)}
        actions={
          <label className="mcp-plugin-enable" htmlFor={toggleId} onClick={(e) => e.stopPropagation()}>
            <span className="general-tab-switch general-tab-switch--compact">
              <input
                ref={targetRef(`settings.mcp.row.${server.id}.toggle`, {
                  kind: "toggle",
                  label: `${server.label} enable`,
                  route: "settings.skills",
                })}
                type="checkbox"
                id={toggleId}
                className="general-tab-switch-input"
                checked={server.enabled}
                disabled={busy || enableBlocked}
                title={
                  enableBlocked
                    ? `Port ${server.http_bind} already used by ${(server.port_conflict_with || []).join(", ")}`
                    : undefined
                }
                onChange={() => void toggleEnabled(server)}
              />
              <span className="general-tab-switch-track" aria-hidden />
            </span>
          </label>
        }
      />
    );
  };

  const isHttpTransport = createTransport === "http" || createTransport === "sse";
  const selectedTest = selectedServer ? testResults[selectedServer.id] : undefined;
  const hasAnyServers = plugins.length > 0;
  const portConflictBanner = useMemo(() => {
    const hits = nestedServers.filter((s) => s.port_conflict || s.enable_blocked_by_port);
    if (!hits.length) return "";
    const binds = Array.from(
      new Set(hits.map((s) => s.http_bind).filter((b): b is string => Boolean(b))),
    );
    return `Only one nested HTTP/SSE MCP may use a host:port. Conflict on ${binds.join(", ") || "shared port"} — disable extras or change a URL (Epic: Editor Preferences → Model Context Protocol).`;
  }, [nestedServers]);

  return (
    <CatalogSlideShell
      className="mcp-plugins-shell"
      detailOpen={detailOpen}
      detailRendered={detailRendered}
      detailPlaceholder={<p>Select an MCP server to view its tools.</p>}
      listAriaLabel="MCP servers"
      listHeader={
        <div className="catalog-slide-header">
          <div className="catalog-slide-header-titles">
            <h2
              className="catalog-slide-title"
              ref={targetRef("settings.mcp.list", {
                kind: "settings_field",
                label: "MCP servers",
                route: "settings.mcp",
              })}
            >
              MCPs
            </h2>
          </div>
          <div className="catalog-slide-header-actions">
            <label className="catalog-slide-search">
              <span className="catalog-slide-search-icon" aria-hidden>
                <Icons.Search />
              </span>
              <input
                className="catalog-slide-search-input"
                type="search"
                placeholder="Search servers…"
                value={listQuery}
                onChange={(e) => setListQuery(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="catalog-slide-action"
              onClick={openConfigEditor}
              title="Edit mcp.json"
              aria-label="Edit mcp.json"
            >
              <Icons.Pencil />
            </button>
            <button
              type="button"
              className="catalog-slide-action"
              onClick={openFolder}
              title="Open folder"
              aria-label="Open folder"
            >
              <Icons.Folder />
            </button>
            <button
              ref={targetRef("settings.mcp.add", { kind: "button", label: "Add server", route: "settings.skills" })}
              type="button"
              className="catalog-slide-action catalog-slide-action--primary"
              onClick={() => setShowCreate(true)}
              title="Add server"
              aria-label="Add server"
            >
              <Icons.Plus />
            </button>
          </div>
        </div>
      }
      listBody={
        !hasAnyServers && !loadError ? (
          <div className="catalog-slide-empty">No MCP servers configured yet.</div>
        ) : (
          <>
            {loadError ? <div className="skills-mcp-load-error">{loadError}</div> : null}
            {portConflictBanner && !loadError ? (
              <div className="skills-mcp-load-error" role="status">
                {portConflictBanner}
              </div>
            ) : null}
            {filterServers(nestedServers).length > 0 ? (
              <ListSection title="MCP servers (nested)">
                {filterServers(nestedServers).map(renderServerRow)}
              </ListSection>
            ) : null}
            {filterServers(builtinGroups).length > 0 ? (
              <ListSection title="Ducky app tools">
                {filterServers(builtinGroups).map(renderServerRow)}
              </ListSection>
            ) : null}
            {filterServers(desktopPluginTools).length > 0 ? (
              <ListSection title="Desktop plugin tools">
                {filterServers(desktopPluginTools).map(renderServerRow)}
              </ListSection>
            ) : null}
            {hasAnyServers &&
            listQuery.trim() &&
            filterServers(nestedServers).length === 0 &&
            filterServers(builtinGroups).length === 0 &&
            filterServers(desktopPluginTools).length === 0 ? (
              <div className="catalog-slide-empty">No servers match your search.</div>
            ) : null}
          </>
        )
      }
      detailHead={
        selectedServer ? (
          <CatalogDetailHead
            breadcrumbs={breadcrumbs}
            onBack={handleDetailBack}
            backAriaLabel={focusId ? "Back to server" : "Back to MCP list"}
            actions={
              <>
                <label className="mcp-plugin-enable" htmlFor={`mcp-detail-enabled-${selectedServer.id}`}>
                  <span className="general-tab-switch general-tab-switch--compact">
                    <input
                      type="checkbox"
                      id={`mcp-detail-enabled-${selectedServer.id}`}
                      className="general-tab-switch-input"
                      checked={selectedServer.enabled}
                      disabled={
                        busyId === selectedServer.id || Boolean(selectedServer.enable_blocked_by_port)
                      }
                      title={
                        selectedServer.enable_blocked_by_port
                          ? `Port ${selectedServer.http_bind} already used by ${(selectedServer.port_conflict_with || []).join(", ")}`
                          : undefined
                      }
                      onChange={() => void toggleEnabled(selectedServer)}
                    />
                    <span className="general-tab-switch-track" aria-hidden />
                  </span>
                  <span className={`mcp-plugin-enable-label${selectedServer.enabled ? " is-on" : ""}`}>
                    {selectedServer.enabled ? "Enabled" : "Disabled"}
                  </span>
                </label>
                <button
                  type="button"
                  className="catalog-slide-action"
                  disabled={busyId === selectedServer.id}
                  onClick={() => void testServer(selectedServer)}
                  title={busyId === selectedServer.id ? "Testing…" : "Test server"}
                  aria-label={busyId === selectedServer.id ? "Testing…" : "Test server"}
                >
                  <Icons.Play />
                </button>
                {canDeleteServer(selectedServer) ? (
                  <button
                    type="button"
                    className="catalog-slide-action catalog-slide-action--danger"
                    disabled={busyId === selectedServer.id}
                    onClick={() => void deleteServer(selectedServer)}
                    title="Delete"
                    aria-label="Delete"
                  >
                    <Icons.Trash />
                  </button>
                ) : null}
              </>
            }
          />
        ) : undefined
      }
      detailBody={
        selectedServer ? (
          <div className="mcp-catalog-detail">
            <h2 className="catalog-slide-detail-title">{selectedServer.label}</h2>
            <div className="catalog-slide-detail-meta">
              <CatalogSourceBadge source={mcpServerSource(selectedServer)} />
              {selectedServer.label !== selectedServer.id ? (
                <code className="mcp-catalog-detail-id">{selectedServer.id}</code>
              ) : null}
              {selectedServer.transport && selectedServer.transport !== "builtin" ? (
                <span>{selectedServer.transport}</span>
              ) : null}
            </div>
            {selectedServer.description?.trim() ? (
              <p className="catalog-slide-detail-overview">{selectedServer.description.trim()}</p>
            ) : null}
            {selectedServer.enable_blocked_by_port || selectedServer.port_conflict ? (
              <p className="catalog-slide-detail-overview skills-mcp-load-error" role="status">
                {selectedServer.enable_blocked_by_port
                  ? `Enable blocked: port ${selectedServer.http_bind} is already used by ${(selectedServer.port_conflict_with || []).join(", ")}. Disable that server first, or change a URL.`
                  : `Port conflict on ${selectedServer.http_bind} with ${(selectedServer.port_conflict_with || []).join(", ")}. Only one HTTP/SSE MCP may own a host:port.`}
              </p>
            ) : null}

            {selectedServer.setup_steps.length > 0 ? (
              <div className="mcp-plugin-setup">
                <h5 className="mcp-plugin-setup-title">Setup instructions</h5>
                <ol className="mcp-plugin-setup-steps">
                  {selectedServer.setup_steps.map((step) => (
                    <li key={step}>{step}</li>
                  ))}
                </ol>
              </div>
            ) : null}

            {selectedTest ? (
              <div className={`mcp-plugin-test-result${selectedTest.ok ? " is-ok" : " is-error"}`}>
                {selectedTest.ok
                  ? `Connected — ${selectedTest.tool_count ?? 0} tools`
                  : `${selectedTest.error ?? "Test failed"}${selectedTest.hint ? ` — ${selectedTest.hint}` : ""}`}
              </div>
            ) : null}

            <McpToolSplitView
              categories={filteredToolCategories}
              allCategories={pluginCategories}
              query={toolQuery}
              onQueryChange={setToolQuery}
              selectedToolName={focusId}
              onSelectTool={setFocusId}
              totalCount={toolTotalCount}
              loading={!catalog}
              emptyMessage={
                !selectedServer.enabled
                  ? "Enable this MCP to load its tools."
                  : toolQuery.trim()
                    ? "No tools match your filter."
                    : "No tools available for this MCP."
              }
            />
          </div>
        ) : undefined
      }
    >
      <AgentCatalogCreateModal
        open={showCreate}
        title="Add MCP server"
        nameLabel="Server id"
        namePlaceholder="e.g. my_tool"
        descriptionLabel="Description"
        descriptionPlaceholder="What does this server do?"
        generateLabel="Suggest setup"
        emptyLabel="Create without suggestions"
        saveLabel="Create server"
        onClose={closeCreate}
        onGenerate={async ({ description }) => {
          const lower = description.toLowerCase();
          const checklist = ["Reviewing description…"];
          if (lower.includes("http") || lower.includes("sse") || lower.includes("url")) {
            checklist.push("Suggested HTTP/SSE transport");
            setCreateTransport(lower.includes("sse") ? "sse" : "http");
          } else {
            checklist.push("Suggested stdio transport");
            setCreateTransport("stdio");
          }
          if (lower.includes("npx")) checklist.push("Hint: npx command");
          if (lower.includes("uvx") || lower.includes("uv ")) checklist.push("Hint: uvx command");
          checklist.push("Ready — fill transport fields below");
          return { checklist };
        }}
        onSave={async ({ name, description }) => {
          if (!name.trim()) throw new Error("Server id is required");
          await createServer(name, description);
          closeCreate();
        }}
        extraFields={
          <div className="mcp-create-form">
            <label className="catalog-slide-create-field">
              <span>Label</span>
              <input
                className="catalog-slide-search-input"
                type="text"
                placeholder="Display name"
                value={createLabel}
                onChange={(e) => setCreateLabel(e.target.value)}
              />
            </label>
            <div className="catalog-slide-create-field">
              <span>Transport</span>
              <TransportDropdown value={createTransport} onChange={setCreateTransport} />
            </div>
            {isHttpTransport ? (
              <>
                <label className="catalog-slide-create-field">
                  <span>URL</span>
                  <input
                    className="catalog-slide-search-input"
                    type="text"
                    placeholder="https://host/mcp"
                    value={createUrl}
                    onChange={(e) => setCreateUrl(e.target.value)}
                  />
                </label>
                <label className="catalog-slide-create-field">
                  <span>Headers</span>
                  <textarea
                    className="catalog-slide-create-textarea"
                    placeholder={"One per line, e.g. Authorization: Bearer ${SECRET:UEFN_TOKEN}"}
                    value={createHeaders}
                    onChange={(e) => setCreateHeaders(e.target.value)}
                    rows={3}
                  />
                </label>
              </>
            ) : (
              <>
                <label className="catalog-slide-create-field">
                  <span>Command</span>
                  <input
                    className="catalog-slide-search-input"
                    type="text"
                    placeholder="e.g. uvx or npx"
                    value={createCommand}
                    onChange={(e) => setCreateCommand(e.target.value)}
                  />
                </label>
                <label className="catalog-slide-create-field">
                  <span>Args</span>
                  <input
                    className="catalog-slide-search-input"
                    type="text"
                    placeholder="Space-separated"
                    value={createArgs}
                    onChange={(e) => setCreateArgs(e.target.value)}
                  />
                </label>
              </>
            )}
          </div>
        }
      />

      <Modal
        open={showConfig}
        onClose={() => setShowConfig(false)}
        title="Edit mcp.json"
        width={720}
        footer={
          <ModalActions
            cancelLabel="Cancel"
            confirmLabel={configSaving ? "Saving…" : "Save"}
            onCancel={() => setShowConfig(false)}
            onConfirm={() => void saveConfig()}
            confirmDisabled={configSaving || !getApi()?.set_mcp_config}
          />
        }
      >
        <div className="mcp-create-form">
          {configPath ? (
            <p className="general-tab-section-desc" style={{ marginTop: 0 }}>
              {configPath}
            </p>
          ) : null}
          <p className="general-tab-section-desc" style={{ marginTop: 0 }}>
            Same shape as Cursor <code>mcp.json</code>.{" "}
            <code>stdio</code> needs <code>command</code>/<code>args</code>;{" "}
            <code>http</code>/<code>sse</code> need <code>url</code> (optional{" "}
            <code>headers</code>). Secrets: <code>{"${SECRET:NAME}"}</code>.
          </p>
          <div className="mcp-config-example">
            <div className="mcp-config-example-head">
              <span className="mcp-create-label">Example</span>
              <button
                type="button"
                className="catalog-slide-action"
                onClick={() => setConfigText(MCP_JSON_EXAMPLE)}
              >
                Insert example
              </button>
            </div>
            <pre className="mcp-config-example-pre" aria-label="mcp.json example">
              {MCP_JSON_EXAMPLE.trimEnd()}
            </pre>
          </div>
          <label className="mcp-create-field">
            <span className="mcp-create-label">Cursor-shaped mcpServers map</span>
            <textarea
              className="settings-input mcp-create-textarea"
              value={configText}
              onChange={(e) => setConfigText(e.target.value)}
              rows={14}
              spellCheck={false}
              style={{ fontFamily: "var(--mono, ui-monospace, monospace)", fontSize: 12 }}
            />
          </label>
          {configError ? <div className="skills-mcp-load-error">{configError}</div> : null}
        </div>
      </Modal>
    </CatalogSlideShell>
  );
}
