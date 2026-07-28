import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { DropdownPanel } from "./DropdownPanel";
import { Icons } from "../icons/Icons";
import { getApi } from "../hooks/usePanelApi";
import { onApiReady } from "../hooks/onApiReady";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import { ScopedCss, useScopedClass } from "../utils/scopedCss";
import { groupByVendor } from "./modelVendors";
import {
  buildPickerGateways,
  gatewayForSelection,
  type PickerGateway,
} from "./modelPickerGateways";
import {
  getCachedDefaultModel,
  getCachedModels,
  installModelsCatalogAutoRefresh,
  isModelsCatalogReady,
  loadModelsCatalog,
  subscribeModelsCatalog,
  type CatalogModelRow,
} from "../hooks/modelsCatalogCache";
import { usePluginContributions } from "../hooks/usePluginContributions";
import type { CodingAgentDto } from "../types/panel";

function formatContext(n: number): string {
  if (!n || n <= 0) return "";
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    return `${m % 1 === 0 ? m.toFixed(0) : m.toFixed(1)}M`;
  }
  return `${Math.round(n / 1000)}K`;
}

function formatUsd(n: number): string {
  return `$${n.toFixed(2).replace(/\.?0+$/, "")}`;
}

function normalizeCodingAgentModelId(_agentId: string, modelId: string): string {
  const mid = (modelId || "").trim();
  return mid.toLowerCase() === "default" ? "auto" : mid;
}

function normId(id: string): string {
  return (id || "").trim().toLowerCase().replace(/-/g, "_");
}

function agentShortLabel(agentId: string, agents: CodingAgentDto[]): string {
  const key = normId(agentId);
  if (key === "ducky") return "Ducky";
  for (const a of agents) {
    if (normId(a.id) === key) {
      const label = String(a.label || "").trim();
      if (label) return label;
    }
  }
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) || "Model";
}

interface ModelSelectorProps {
  selectedModel: string;
  setSelectedModel: (id: string) => void;
  codingAgent?: string;
  setCodingAgent?: (id: string) => void;
  convId?: string;
  onLoadingChange?: (loading: boolean) => void;
  onModelMetaChange?: (meta: { supportsVision: boolean; name: string }) => void;
  /** Hide moderation / chat-only models unsuitable for text generation. */
  requireTools?: boolean;
  /** Fixed catalog (e.g. favorites picker baseline). When set, skips live-only loading. */
  catalogRows?: CatalogModelRow[];
  /** Keep the current id even when it is not in the live provider catalog. */
  preserveSelection?: boolean;
  /** Where the dropdown opens relative to the trigger. */
  menuPlacement?: "top" | "bottom";
  /** Trigger text when nothing is selected. */
  placeholder?: string;
}

export function ModelSelector({
  selectedModel,
  setSelectedModel,
  codingAgent = "ducky",
  setCodingAgent,
  convId,
  onLoadingChange,
  onModelMetaChange,
  requireTools = false,
  catalogRows,
  preserveSelection = false,
  menuPlacement = "top",
  placeholder = "Pick a model",
}: ModelSelectorProps) {
  const contrib = usePluginContributions();
  const [isOpen, setIsOpen] = useState(false);
  const [models, setModels] = useState<CatalogModelRow[]>(() => getCachedModels() ?? []);
  const [agents, setAgents] = useState<CodingAgentDto[]>([]);
  const [search, setSearch] = useState("");
  // null = gateway list; provider key = that gateway’s models (+ nested CLIs).
  const [navGateway, setNavGateway] = useState<string | null>(null);
  const [openVendor, setOpenVendor] = useState<string | null>(null);
  const [viewportH, setViewportH] = useState<number>();
  const anchorRef = useRef<HTMLButtonElement>(null);
  const gatewaysPageRef = useRef<HTMLDivElement>(null);
  const modelsPageRef = useRef<HTMLDivElement>(null);
  const firstHitRef = useRef<HTMLDivElement>(null);
  const navScope = useScopedClass("model-selector-nav");

  const navGatewayRef = useRef<string | null>(null);
  const openRef = useRef(false);
  const histCountRef = useRef(0);
  const suppressPopRef = useRef(false);

  const applyDefaultSelection = useCallback(
    (_allModels: CatalogModelRow[], _defaultModel: string, _current: string) => {
      // Never invent a model.
    },
    [],
  );

  const visibleModels = useMemo(
    () => (requireTools ? models.filter((m) => m.supportsTools) : models),
    [models, requireTools],
  );

  const gateways = useMemo(
    () => buildPickerGateways(contrib.llm_providers, agents, contrib.llm_coding_agents),
    [contrib.llm_providers, contrib.llm_coding_agents, agents],
  );

  const syncFromCatalog = useCallback(
    (allModels: CatalogModelRow[]) => {
      setModels(allModels);
      if (preserveSelection) return;
      if (codingAgent !== "ducky") return;
      const pool = requireTools ? allModels.filter((m) => m.supportsTools) : allModels;
      applyDefaultSelection(pool, getCachedDefaultModel(), selectedModel);
    },
    [applyDefaultSelection, codingAgent, preserveSelection, requireTools, selectedModel],
  );

  useEffect(() => {
    if (catalogRows) {
      setModels(catalogRows);
    }
  }, [catalogRows]);

  const loadModels = useCallback(
    async (options?: { force?: boolean }) => {
      const hadCache = isModelsCatalogReady();
      if (!hadCache) onLoadingChange?.(true);
      try {
        const allModels = await loadModelsCatalog(options);
        syncFromCatalog(allModels);
      } finally {
        if (!hadCache) onLoadingChange?.(false);
      }
    },
    [onLoadingChange, syncFromCatalog],
  );

  const loadAgents = useCallback(async () => {
    const api = getApi();
    if (!api?.list_coding_agents) return;
    try {
      const res = await api.list_coding_agents();
      setAgents(res.agents || []);
    } catch {
      setAgents([]);
    }
  }, []);

  useEffect(() => {
    if (catalogRows) return;
    return subscribeModelsCatalog(() => {
      const cached = getCachedModels();
      if (cached !== null) syncFromCatalog(cached);
    });
  }, [catalogRows, syncFromCatalog]);

  useEffect(() => {
    if (catalogRows) {
      onLoadingChange?.(false);
      return;
    }
    installModelsCatalogAutoRefresh();
    return onApiReady(() => {
      void loadAgents();
      if (isModelsCatalogReady()) {
        syncFromCatalog(getCachedModels() ?? []);
        onLoadingChange?.(false);
        return;
      }
      void loadModels();
    });
  }, [catalogRows, loadAgents, loadModels, onLoadingChange, syncFromCatalog]);

  useEffect(() => {
    if (catalogRows) return;
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type === "key_test_done" && event.ok) {
        void loadAgents();
        return;
      }
      if (event.type !== "uefn_plugins_changed") return;
      void loadAgents();
    });
  }, [catalogRows, loadAgents]);

  const agentModels = useCallback(
    (agentId: string): CatalogModelRow[] => {
      const agent = agents.find((a) => normId(a.id) === normId(agentId));
      return (agent?.models || []).map(
        (m): CatalogModelRow => ({
          id: normalizeCodingAgentModelId(agentId, m.id),
          name: m.name,
          provider: m.provider || agent?.label || agentId,
          providerKey: agentId,
          supportsVision: false,
          supportsTools: true,
          supportsWebSearch: false,
          contextLimit: 0,
          priceIn: null,
          priceOut: null,
          isLocal: false,
        }),
      );
    },
    [agents],
  );

  /** API models for a gateway only (no Cursor/Anthropic mix under OpenAI). */
  const apiModelsForGateway = useCallback(
    (gw: PickerGateway): CatalogModelRow[] => {
      if (gw.primaryAgentId) {
        const fromAgent = agentModels(gw.primaryAgentId);
        if (fromAgent.length) return fromAgent;
      }
      return visibleModels.filter((m) => normId(m.providerKey) === gw.providerKey);
    },
    [agentModels, visibleModels],
  );

  const agentIsUnavailable = useCallback(
    (agentId: string) => {
      if (normId(agentId) === "ducky") return false;
      const a = agents.find((row) => normId(row.id) === normId(agentId));
      if (!a) return true;
      return !a.enabled || !a.available;
    },
    [agents],
  );

  // Gateway picker when we can switch coding agents and gateways exist; else flat catalog.
  const twoLevel = !!setCodingAgent && !catalogRows && gateways.length > 0;
  const singleAgent = codingAgent || "ducky";

  const selectedProviderKey = useMemo(() => {
    if (codingAgent !== "ducky") return "";
    const hit = visibleModels.find((m) => m.id === selectedModel);
    return hit?.providerKey || "";
  }, [codingAgent, selectedModel, visibleModels]);

  const normalizedSelectedModel = normalizeCodingAgentModelId(codingAgent, selectedModel);
  const committedModels =
    codingAgent !== "ducky"
      ? agentModels(codingAgent)
      : selectedProviderKey
        ? visibleModels.filter((m) => normId(m.providerKey) === normId(selectedProviderKey))
        : visibleModels;
  const currentModelData = committedModels.find((m) => m.id === normalizedSelectedModel);
  const catalogReady = codingAgent !== "ducky" || isModelsCatalogReady();
  const agentLabel = agentShortLabel(codingAgent, agents);
  const displayName =
    codingAgent !== "ducky"
      ? currentModelData
        ? `${agentLabel} · ${currentModelData.name}`
        : normalizedSelectedModel
          ? `${agentLabel} · ${normalizedSelectedModel === "auto" ? "Auto" : normalizedSelectedModel}`
          : agentLabel
      : committedModels.length === 0 && catalogReady && visibleModels.length === 0
        ? "No models"
        : currentModelData?.name ?? (selectedModel || placeholder);

  useEffect(() => {
    onModelMetaChange?.({
      supportsVision: codingAgent === "ducky" ? !!currentModelData?.supportsVision : false,
      name: displayName,
    });
  }, [codingAgent, currentModelData?.supportsVision, displayName, onModelMetaChange]);

  const canOpen = gateways.length > 0 || models.length > 0 || agents.length > 0;
  const query = search.trim().toLowerCase();

  const activeGateway = useMemo(
    () => gateways.find((g) => g.id === navGateway) || null,
    [gateways, navGateway],
  );

  const viewApiRows = useMemo(() => {
    if (!activeGateway) return [];
    const base = apiModelsForGateway(activeGateway);
    return query ? base.filter((m) => m.name.toLowerCase().includes(query)) : base;
  }, [activeGateway, apiModelsForGateway, query]);

  const selectedRowId =
    codingAgent === "ducky" && activeGateway && normId(selectedProviderKey) === activeGateway.providerKey
      ? normalizedSelectedModel
      : codingAgent !== "ducky" &&
          activeGateway &&
          (activeGateway.primaryAgentId === normId(codingAgent) ||
            activeGateway.nestedAgents.some((a) => normId(a.id) === normId(codingAgent)))
        ? normalizedSelectedModel
        : null;

  const gatewaySearchHits = useMemo(() => {
    if (!query) return [];
    return gateways
      .map((gw) => {
        const nameHit = gw.label.toLowerCase().includes(query);
        const api = apiModelsForGateway(gw).filter((m) => m.name.toLowerCase().includes(query));
        const nested = gw.nestedAgents
          .map((a) => ({
            agent: a,
            models: agentModels(a.id).filter((m) => m.name.toLowerCase().includes(query)),
            labelHit: (a.label || "").toLowerCase().includes(query),
          }))
          .filter((x) => x.models.length > 0 || x.labelHit);
        return { gateway: gw, nameHit, api, nested };
      })
      .filter((h) => h.nameHit || h.api.length > 0 || h.nested.length > 0);
  }, [query, gateways, apiModelsForGateway, agentModels]);

  const setNav = useCallback((g: string | null) => {
    navGatewayRef.current = g;
    setNavGateway(g);
  }, []);

  const pushHist = useCallback(() => {
    try {
      window.history.pushState({ msNav: true }, "");
      histCountRef.current += 1;
    } catch {
      /* history unavailable */
    }
  }, []);

  const closeNow = useCallback(() => {
    openRef.current = false;
    setIsOpen(false);
    setNav(null);
    setSearch("");
    setOpenVendor(null);
    setViewportH(undefined);
  }, [setNav]);

  const requestClose = useCallback(() => {
    const n = histCountRef.current;
    histCountRef.current = 0;
    if (n > 0) {
      try {
        suppressPopRef.current = true;
        window.history.go(-n);
      } catch {
        suppressPopRef.current = false;
      }
    }
    closeNow();
  }, [closeNow]);

  const goBack = useCallback(() => {
    if (histCountRef.current > 0) {
      try {
        window.history.back();
        return;
      } catch {
        /* fall through */
      }
    }
    if (navGatewayRef.current) {
      setNav(null);
      setOpenVendor(null);
    } else {
      requestClose();
    }
  }, [setNav, requestClose]);

  const drillInto = useCallback(
    (gatewayId: string) => {
      setNav(gatewayId);
      setOpenVendor(null);
      pushHist();
    },
    [setNav, pushHist],
  );

  const openDropdown = () => {
    if (!canOpen) return;
    openRef.current = true;
    suppressPopRef.current = false;
    setSearch("");
    const current = gatewayForSelection(gateways, codingAgent, selectedProviderKey);
    setNav(twoLevel ? current?.id ?? null : null);
    setOpenVendor(null);
    setIsOpen(true);
    histCountRef.current = 0;
    pushHist();
    void loadAgents();
  };

  useEffect(() => {
    const onPop = () => {
      if (suppressPopRef.current) {
        suppressPopRef.current = false;
        return;
      }
      if (!openRef.current) return;
      if (histCountRef.current > 0) histCountRef.current -= 1;
      if (navGatewayRef.current) {
        setNav(null);
        setOpenVendor(null);
      } else {
        closeNow();
      }
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [setNav, closeNow]);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        goBack();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [isOpen, goBack]);

  useLayoutEffect(() => {
    if (!isOpen || !twoLevel) return;
    const el = navGateway ? modelsPageRef.current : gatewaysPageRef.current;
    if (!el) return;
    const measure = () => setViewportH(el.getBoundingClientRect().height);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [isOpen, twoLevel, navGateway, viewApiRows, openVendor, search, gatewaySearchHits]);

  useLayoutEffect(() => {
    if (!isOpen || !query) return;
    firstHitRef.current?.scrollIntoView({ block: "nearest" });
  }, [isOpen, query, viewApiRows, navGateway, openVendor, gatewaySearchHits]);

  const pickModel = useCallback(
    (row: CatalogModelRow, agentId: string, providerKey?: string) => {
      if (agentIsUnavailable(agentId) && agentId !== "ducky") return;
      const api = getApi();
      setCodingAgent?.(agentId);
      setSelectedModel(row.id);
      if (agentId !== "ducky") {
        if (api && convId && api.set_conversation_coding_agent) {
          void api.set_conversation_coding_agent(convId, agentId, row.id);
        }
      } else if (!preserveSelection) {
        const pk = providerKey || row.providerKey || "";
        if (api && convId && api.set_conversation_coding_agent) {
          void api.set_conversation_coding_agent(convId, "ducky", row.id, pk);
        } else if (api && !convId) {
          void api.set_model(row.id, pk);
        }
      } else if (setCodingAgent && agentId !== codingAgent && api && convId && api.set_conversation_coding_agent) {
        void api.set_conversation_coding_agent(convId, "ducky");
      }
      requestClose();
    },
    [
      codingAgent,
      convId,
      preserveSelection,
      setSelectedModel,
      setCodingAgent,
      requestClose,
      agentIsUnavailable,
    ],
  );

  const renderRow = (
    m: CatalogModelRow,
    opts: { agentId: string; selected?: boolean; hitRef?: boolean; disabled?: boolean },
  ) => {
    const isSel = opts.selected ?? false;
    const disabled = !!opts.disabled;
    return (
      <div
        key={`${opts.agentId}:${m.id}`}
        ref={opts.hitRef ? firstHitRef : undefined}
        className={`model-selector-option${isSel ? " is-selected" : ""}${disabled ? " is-disabled" : ""}`}
        title={disabled ? "Unavailable — enable in Settings → LLMs" : undefined}
        onClick={() => {
          if (!disabled) pickModel(m, opts.agentId, m.providerKey);
        }}
      >
        <span className="model-selector-option-name" title={m.name}>
          {m.name}
        </span>
        <span className="model-selector-option-meta">
          {m.isLocal ? (
            <span className="model-selector-price is-free" title="Local model — no API cost">
              Free
            </span>
          ) : m.priceIn != null && m.priceOut != null ? (
            <span
              className="model-selector-price"
              title={`USD per 1M tokens — input ${formatUsd(m.priceIn)}, output ${formatUsd(m.priceOut)}`}
            >
              {formatUsd(m.priceIn)}/{formatUsd(m.priceOut)}
            </span>
          ) : null}
          {m.contextLimit > 0 && (
            <span
              className="model-selector-ctx"
              title={`Context window: ${m.contextLimit.toLocaleString()} tokens`}
            >
              {formatContext(m.contextLimit)}
            </span>
          )}
          {m.supportsVision && (
            <span className="model-selector-cap-badge" title="Multimodal — accepts images">
              📷
            </span>
          )}
          {m.supportsWebSearch && (
            <span className="model-selector-cap-badge" title="Web search — can browse online">
              🌐
            </span>
          )}
          {m.supportsTools ? (
            <span className="model-selector-cap-badge" title="Supports tools — can run automated agent mode">
              🔧
            </span>
          ) : (
            <span
              className="model-selector-cap-badge is-dim"
              title="No tool support — chat / questions only, not automated agent work"
            >
              💬
            </span>
          )}
          {isSel && (
            <span className="model-selector-check">
              <Icons.Check />
            </span>
          )}
        </span>
      </div>
    );
  };

  const renderFlatOrVendor = (rows: CatalogModelRow[], agentId: string, selectedId: string | null) => {
    const groups = groupByVendor(rows);
    const single = groups.length <= 1;
    let firstHit = true;
    return (
      <>
        {groups.map(({ vendor, rows: groupRows }) => {
          const collapsed = !single && !query && openVendor !== vendor;
          const showRows = single || !!query || openVendor === vendor;
          return (
            <div key={vendor} className="model-selector-provider-group">
              {!single && (
                <button
                  type="button"
                  className={`model-selector-provider-label${collapsed ? " is-collapsed" : ""}`}
                  onClick={() => setOpenVendor((v) => (v === vendor ? null : vendor))}
                >
                  <span className="model-selector-provider-caret">
                    <Icons.ChevronDown />
                  </span>
                  <span className="model-selector-provider-name">{vendor}</span>
                  <span className="model-selector-provider-count">{groupRows.length}</span>
                </button>
              )}
              {showRows &&
                groupRows.map((m) => {
                  const hitRef = !!query && firstHit;
                  if (hitRef) firstHit = false;
                  return renderRow(m, {
                    agentId,
                    selected: selectedId === m.id && agentId === codingAgent,
                    hitRef,
                    disabled: agentIsUnavailable(agentId) && agentId !== "ducky",
                  });
                })}
            </div>
          );
        })}
        {rows.length === 0 && (
          <div className="model-selector-empty">{query ? `No models match “${search}”.` : "No models."}</div>
        )}
      </>
    );
  };

  const renderGatewayDetail = (gw: PickerGateway) => {
    const apiAgentId = gw.primaryAgentId || "ducky";
    const apiSelected =
      codingAgent === apiAgentId || (apiAgentId === "ducky" && codingAgent === "ducky")
        ? selectedRowId
        : null;
    return (
      <>
        {renderFlatOrVendor(viewApiRows, apiAgentId, apiSelected)}
        {gw.nestedAgents.map((agent) => {
          const rows = agentModels(agent.id);
          const filtered = query ? rows.filter((m) => m.name.toLowerCase().includes(query)) : rows;
          const unavailable = agentIsUnavailable(agent.id);
          return (
            <div key={agent.id} className="model-selector-provider-group">
              <div className="model-selector-provider-label is-static">
                <span className="model-selector-provider-name">{agent.label}</span>
                {unavailable ? (
                  <span className="model-selector-unavailable-tag">Unavailable</span>
                ) : (
                  <span className="model-selector-provider-count">{filtered.length}</span>
                )}
              </div>
              {filtered.map((m) =>
                renderRow(m, {
                  agentId: agent.id,
                  selected: codingAgent === agent.id && m.id === normalizedSelectedModel,
                  disabled: unavailable,
                }),
              )}
              {filtered.length === 0 && !unavailable ? (
                <div className="model-selector-empty">No models.</div>
              ) : null}
            </div>
          );
        })}
      </>
    );
  };

  const searchBox = () => (
    <div className="model-selector-search-wrap">
      <input
        className="model-selector-search"
        type="text"
        placeholder="Search models…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        autoFocus
      />
    </div>
  );

  const renderGatewayList = () => {
    if (query) {
      let firstHit = true;
      return (
        <>
          {gatewaySearchHits.map(({ gateway: gw, api, nested }) => (
            <div key={gw.id} className="model-selector-provider-group">
              <button
                type="button"
                className="model-selector-provider-label"
                onClick={() => drillInto(gw.id)}
              >
                <span className="model-selector-provider-caret">
                  <Icons.ChevronRight />
                </span>
                <span className="model-selector-provider-name">{gw.label}</span>
              </button>
              {api.map((m) => {
                const hitRef = firstHit;
                if (hitRef) firstHit = false;
                return renderRow(m, {
                  agentId: gw.primaryAgentId || "ducky",
                  selected:
                    (gw.primaryAgentId ? codingAgent === gw.primaryAgentId : codingAgent === "ducky") &&
                    m.id === normalizedSelectedModel,
                  hitRef,
                });
              })}
              {nested.map(({ agent, models: ms }) =>
                ms.map((m) => {
                  const hitRef = firstHit;
                  if (hitRef) firstHit = false;
                  return renderRow(m, {
                    agentId: agent.id,
                    selected: codingAgent === agent.id && m.id === normalizedSelectedModel,
                    hitRef,
                    disabled: agentIsUnavailable(agent.id),
                  });
                }),
              )}
            </div>
          ))}
          {gatewaySearchHits.length === 0 && (
            <div className="model-selector-empty">No models match “{search}”.</div>
          )}
        </>
      );
    }

    return (
      <div className="model-selector-provider-group">
        <div className="model-selector-provider-label is-static">
          <span className="model-selector-provider-name">Gateway</span>
        </div>
        {gateways.map((gw) => {
          const sel = gatewayForSelection(gateways, codingAgent, selectedProviderKey)?.id === gw.id;
          const count =
            apiModelsForGateway(gw).length +
            gw.nestedAgents.reduce((n, a) => n + agentModels(a.id).length, 0);
          return (
            <div
              key={gw.id}
              className={`model-selector-option${sel ? " is-selected" : ""}`}
              onClick={() => drillInto(gw.id)}
            >
              <span className="model-selector-option-name">{gw.label}</span>
              <span className="model-selector-option-meta">
                {count > 0 ? <span className="model-selector-provider-count">{count}</span> : null}
                {sel && (
                  <span className="model-selector-check">
                    <Icons.Check />
                  </span>
                )}
                <span className="model-selector-disclosure">
                  <Icons.ChevronRight />
                </span>
              </span>
            </div>
          );
        })}
      </div>
    );
  };

  // Flat catalog fallback (favorites / no gateways yet).
  const flatRows = useMemo(() => {
    if (twoLevel) return [];
    const base =
      codingAgent !== "ducky" ? agentModels(singleAgent) : visibleModels;
    return query ? base.filter((m) => m.name.toLowerCase().includes(query)) : base;
  }, [twoLevel, codingAgent, singleAgent, agentModels, visibleModels, query]);

  return (
    <div className="ui-relative">
      <button
        ref={anchorRef}
        type="button"
        className={`no-drag model-selector-btn${isOpen ? " is-open" : ""}`}
        onClick={() => (isOpen ? requestClose() : openDropdown())}
        disabled={!canOpen}
        title={codingAgent !== "ducky" ? `${agentLabel}` : undefined}
      >
        <span>{displayName}</span>
        {(canOpen || selectedModel) && <Icons.ChevronDown />}
      </button>

      <DropdownPanel
        anchorRef={anchorRef}
        open={isOpen && canOpen}
        onClose={requestClose}
        placement={menuPlacement}
        minWidth={300}
        width={320}
      >
        {twoLevel ? (
          <div className={`model-selector-nav ${navScope}`} data-level={navGateway ? 1 : 0}>
            {viewportH != null ? (
              <ScopedCss selector={`.${navScope}`} rules={{ "--ms-viewport-h": `${viewportH}px` }} />
            ) : null}
            <div className="model-selector-navhdr">
              {navGateway && activeGateway ? (
                <button
                  type="button"
                  className="model-selector-back"
                  onClick={goBack}
                  aria-label="Back to gateways"
                >
                  <span className="model-selector-back-caret">
                    <Icons.ChevronRight />
                  </span>
                  <span>{activeGateway.label}</span>
                </button>
              ) : (
                <span className="model-selector-navhdr-title">Pick model</span>
              )}
            </div>
            {searchBox()}
            <div className="model-selector-viewport">
              <div className="model-selector-track">
                <div className="model-selector-page" ref={gatewaysPageRef}>
                  <div className="model-selector-scroll">{renderGatewayList()}</div>
                </div>
                <div className="model-selector-page" ref={modelsPageRef}>
                  <div className="model-selector-scroll">
                    {activeGateway ? renderGatewayDetail(activeGateway) : null}
                  </div>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <>
            {searchBox()}
            <div className="model-selector-scroll" ref={modelsPageRef}>
              {renderFlatOrVendor(flatRows, singleAgent, normalizedSelectedModel)}
            </div>
          </>
        )}
      </DropdownPanel>
    </div>
  );
}
