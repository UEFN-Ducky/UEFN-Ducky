import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { BillingBadge } from "./BillingBadge";
import { billingKind } from "../utils/billing";
import { DropdownPanel } from "./DropdownPanel";
import { Icons } from "../icons/Icons";
import { getApi } from "../hooks/usePanelApi";
import { skipAutoFocus } from "../hooks/useNarrowLayout";
import { setCachedCodingAgents } from "../hooks/codingAgentsCache";
import { hasUsableModels } from "./ducky/duckyPickerIssue";
import { onApiReady } from "../hooks/onApiReady";
import { installPanelPushBus, subscribePanelPush } from "../hooks/usePanelPushBus";
import { groupByVendor } from "./modelVendors";
import { ModelSection } from "./ModelSection";
import {
  buildPickerGateways,
  gatewayForSelection,
  type PickerGateway,
} from "./modelPickerGateways";
import {
  getCachedModels,
  installModelsCatalogAutoRefresh,
  isModelsCatalogReady,
  loadModelsCatalog,
  subscribeModelsCatalog,
  type CatalogModelRow,
} from "../hooks/modelsCatalogCache";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { useMergedRef, useUiTarget } from "../ui-targets/registry";
import type { CodingAgentDto } from "../types/panel";
import { ThinkingEffortFooter } from "./ThinkingEffortFooter";
import {
  effortGlow,
  GatewayUsageFooter,
  type UsageNotice,
  type UsageSwitch,
  type UsageWindow,
} from "./GatewayUsageFooter";
import { catalogThinkingMenu, effortInMenu, effortSuffix } from "./thinkingMenu";
import { openCodingAgentLoginUi } from "../walkthrough/openCodingAgentLogin";
import { openDefaultModelSettings, openLlmsProviderSettings } from "../navigation/openSettingsTab";
import { LlmPluginSlot } from "../plugin-ui/llmPluginSlots";

/** Same as the backend usage cache (model_fetch._USAGE_TTL_S). */
const USAGE_RECHECK_MS = 60_000;

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

function SettingsGear({ label, onOpen }: { label: string; onOpen: () => void }) {
  return (
    <button
      type="button"
      className="model-selector-gear"
      title={`${label} settings`}
      aria-label={`Open ${label} settings`}
      onClick={(e) => {
        e.stopPropagation();
        e.preventDefault();
        onOpen();
      }}
    >
      <Icons.Settings />
    </button>
  );
}

function catalogRowFor(id: string, rows: CatalogModelRow[]): CatalogModelRow | undefined {
  const mid = (id || "").trim();
  if (!mid) return undefined;
  const bare = mid.includes(":") ? mid.slice(mid.indexOf(":") + 1) : mid;
  return (
    rows.find((c) => c.id === mid) ||
    rows.find((c) => c.id === bare) ||
    rows.find((c) => c.id.endsWith(`:${bare}`))
  );
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
  /** Bump to open the dropdown from outside (the composer's `/model` command). */
  openSignal?: number;
  /** Spotlight id when this trigger is the chat composer control. */
  uiTarget?: string;
  thinkingEffort?: string;
  onEffortChange?: (effort: string) => void;
  /** A full-width trigger that names the model and its gateway (forms), not just the logo. */
  labeled?: boolean;
  /** Stacking for the menu when the picker sits in a layer above the app (the setup). */
  menuZIndex?: number;
  /** A plugin's own rows; a providerKey no gateway has becomes its own gateway, listed first. */
  extraRows?: CatalogModelRow[];
  /** Only models that can read pictures (extra rows say so themselves). */
  requireVision?: boolean;
  /** API models only: no coding agents (Claude Code, Codex) under the gateways. */
  apiOnly?: boolean;
  /** Gateways whose own models are left out; one still shows when an extra row is under it. */
  hideProviders?: string[];
  /** Told when the menu opens or closes. */
  onOpenChange?: (open: boolean) => void;
  /** Close the menu once a model is picked. */
  closeOnPick?: boolean;
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
  openSignal = 0,
  uiTarget = "",
  thinkingEffort = "off",
  onEffortChange,
  labeled = false,
  menuZIndex,
  extraRows,
  requireVision = false,
  apiOnly = false,
  hideProviders,
  onOpenChange,
  closeOnPick = false,
}: ModelSelectorProps) {
  const contrib = usePluginContributions();
  const [isOpen, setIsOpen] = useState(false);
  const [models, setModels] = useState<CatalogModelRow[]>(() => getCachedModels() ?? []);
  const [agents, setAgents] = useState<CodingAgentDto[]>([]);
  const [agentsChecking, setAgentsChecking] = useState(true);
  const [search, setSearch] = useState("");
  // null = gateway list; provider key = that gateway’s models (+ nested CLIs).
  const [navGateway, setNavGateway] = useState<string | null>(null);
  const [openVendor, setOpenVendor] = useState<string | null>(null);
  const [usageWindows, setUsageWindows] = useState<UsageWindow[]>([]);
  const [usageNotice, setUsageNotice] = useState<UsageNotice | null>(null);
  const [usageSwitch, setUsageSwitch] = useState<UsageSwitch | null>(null);
  const [usageBusy, setUsageBusy] = useState(false);
  const [usageTick, setUsageTick] = useState(0);
  // This chat's account label, shown as a chip when its gateway has 2+ accounts ("" = none).
  const [chatAccount, setChatAccount] = useState("");
  const usageForceRef = useRef(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const uiTargetRef = useUiTarget(uiTarget, { kind: "dropdown", label: "Model", route: "chat" });
  const triggerRef = useMergedRef(anchorRef, uiTargetRef);
  // The open menu, so a tour can walk into it.
  const menuTargetRef = useUiTarget(uiTarget ? `${uiTarget}.menu` : "", { kind: "dropdown", label: "Model menu", route: "chat" });
  const firstHitRef = useRef<HTMLDivElement>(null);

  const navGatewayRef = useRef<string | null>(null);
  const openRef = useRef(false);
  const histCountRef = useRef(0);
  const suppressPopRef = useRef(false);

  const hidden = useMemo(() => new Set((hideProviders ?? []).map(normId)), [hideProviders]);

  const visibleModels = useMemo(() => {
    const base = models.filter(
      (m) =>
        (!requireTools || m.supportsTools) &&
        (!requireVision || m.supportsVision) &&
        !hidden.has(normId(m.providerKey)),
    );
    return extraRows?.length ? [...extraRows, ...base] : base;
  }, [models, requireTools, requireVision, extraRows, hidden]);

  const gateways = useMemo(() => {
    let out = buildPickerGateways(contrib.llm_providers, agents, contrib.llm_coding_agents);
    if (apiOnly) out = out.map((g) => ({ ...g, nestedAgents: [], primaryAgentId: null }));
    if (hidden.size) {
      const kept = new Set((extraRows ?? []).map((row) => normId(row.providerKey)));
      out = out.filter((g) => !hidden.has(g.providerKey) || kept.has(g.providerKey));
    }
    const own: PickerGateway[] = [];
    for (const row of extraRows ?? []) {
      const key = normId(row.providerKey);
      if (!key || out.some((g) => g.providerKey === key) || own.some((g) => g.providerKey === key)) continue;
      own.push({ id: key, label: row.provider, providerKey: key, pluginId: "", order: -1, nestedAgents: [], primaryAgentId: null, showsThinkingEffort: false });
    }
    return [...own, ...out];
  }, [contrib.llm_providers, contrib.llm_coding_agents, agents, apiOnly, extraRows, hidden]);

  const syncFromCatalog = useCallback((allModels: CatalogModelRow[]) => {
    setModels(allModels);
  }, []);

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
      setAgentsChecking(!!res.checking);
      // Published for the plain form helpers, which have no component to thread
      // this through but still have to tell a coding agent from a provider.
      setCachedCodingAgents(res.agents || []);
    } catch {
      setAgents([]);
      setAgentsChecking(false);
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
      if (event.type === "coding_agents_updated" || event.type === "uefn_plugins_changed") {
        void loadAgents();
      }
    });
  }, [catalogRows, loadAgents]);

  const agentModels = useCallback(
    (agentId: string): CatalogModelRow[] => {
      const agent = agents.find((a) => normId(a.id) === normId(agentId));
      return (agent?.models || []).map((m): CatalogModelRow => {
        const catalog = catalogRowFor(m.id, visibleModels);
        return {
          id: normalizeCodingAgentModelId(agentId, m.id),
          name: m.name,
          provider: m.provider || agent?.label || agentId,
          providerKey: agentId,
          supportsVision: m.supports_vision ?? catalog?.supportsVision ?? false,
          supportsTools: m.supports_tools ?? catalog?.supportsTools ?? true,
          supportsWebSearch: m.supports_web_search ?? catalog?.supportsWebSearch ?? false,
          contextLimit: m.context_limit ?? catalog?.contextLimit ?? 0,
          priceIn: m.price_in ?? catalog?.priceIn ?? null,
          priceOut: m.price_out ?? catalog?.priceOut ?? null,
          isLocal: false,
          thinkingMenu: catalog?.thinkingMenu ?? (m.thinking_menu?.levels?.length ? m.thinking_menu : null),
        };
      });
    },
    [agents, visibleModels],
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
      if (agentsChecking) return false;
      const a = agents.find((row) => normId(row.id) === normId(agentId));
      // Empty list is "not loaded yet" (just installed a gateway), not unavailable.
      if (!a) return agents.length > 0;
      return !a.enabled || !a.available;
    },
    [agents, agentsChecking],
  );

  // Gateway picker when we can switch coding agents and gateways exist; else flat catalog.
  const twoLevel = (!!setCodingAgent || apiOnly) && !catalogRows && gateways.length > 0;
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
  const selectedMenu = catalogThinkingMenu(currentModelData);

  const persistEffort = useCallback(
    (next: string) => {
      onEffortChange?.(next);
      if (!convId) return;
      void getApi()?.set_conversation_thinking_effort?.(convId, next);
    },
    [convId, onEffortChange],
  );

  useEffect(() => {
    if (!selectedMenu) return;
    const snapped = effortInMenu(selectedMenu, thinkingEffort);
    if (snapped !== (thinkingEffort || "off").trim().toLowerCase()) persistEffort(snapped);
  }, [selectedMenu, thinkingEffort, persistEffort]);
  const catalogReady = codingAgent !== "ducky" || isModelsCatalogReady();
  const agentLabel = agentShortLabel(codingAgent, agents);
  const displayName =
    codingAgent !== "ducky"
      ? currentModelData
        ? `${agentLabel} · ${currentModelData.name}`
        : normalizedSelectedModel
          ? `${agentLabel} · ${normalizedSelectedModel === "auto" ? "Auto" : normalizedSelectedModel}`
          : agentLabel
      : committedModels.length === 0 &&
          catalogReady &&
          !agentsChecking &&
          !hasUsableModels({ modelsCount: visibleModels.length, agents })
        ? "No models"
        : currentModelData?.name ?? (selectedModel || placeholder);
  const triggerLabel = `${displayName}${effortSuffix(selectedMenu, thinkingEffort)}`;
  // How this pick is paid: a subscription login, a pay-per-token API key, this PC or the account.
  const billing = selectedModel
    ? billingKind(codingAgent, currentModelData?.providerKey || selectedProviderKey, Boolean(currentModelData?.isLocal))
    : "";

  useEffect(() => {
    onModelMetaChange?.({
      supportsVision: codingAgent === "ducky" ? !!currentModelData?.supportsVision : false,
      name: displayName,
    });
  }, [codingAgent, currentModelData?.supportsVision, displayName, onModelMetaChange]);

  const canOpen = gateways.length > 0 || models.length > 0 || agents.length > 0;
  const query = search.trim().toLowerCase();
  const selectedGateway = useMemo(
    () => gatewayForSelection(gateways, codingAgent, selectedProviderKey),
    [gateways, codingAgent, selectedProviderKey],
  );
  const activeGateway = useMemo(
    () => gateways.find((g) => g.id === navGateway) || null,
    [gateways, navGateway],
  );
  // A chat on a gateway's coding agent (Claude Code under Anthropic, Codex under OpenAI) uses
  // that agent's logins, not the gateway's API keys: ask core with the agent id.
  const accountsOf = (gw: PickerGateway | null | undefined): string => {
    if (!gw) return "";
    const agent = normId(codingAgent);
    const ownsAgent =
      agent !== "ducky" &&
      (normId(gw.primaryAgentId || "") === agent || gw.nestedAgents.some((a) => normId(a.id) === agent));
    return (ownsAgent ? agent : gw.providerKey || "").trim();
  };
  const usageProvider = accountsOf(activeGateway) || accountsOf(selectedGateway);
  const glow = effortGlow(selectedMenu, thinkingEffort);
  const usageByProviderRef = useRef<
    Record<string, { windows: UsageWindow[]; notice: UsageNotice | null; switch: UsageSwitch | null; at: number }>
  >({});

  // In a chat the bars and switch are that chat's account, so the cache is per chat too.
  const usageCacheKey = convId ? `${usageProvider}@${convId}` : usageProvider;
  const chipProvider = accountsOf(selectedGateway);

  useEffect(() => {
    const api = getApi();
    if (!convId || !chipProvider || !api?.get_chat_account || !api.list_gateway_accounts) {
      setChatAccount("");
      return;
    }
    let cancelled = false;
    void Promise.all([api.list_gateway_accounts(chipProvider), api.get_chat_account(convId, chipProvider)]).then(
      ([list, mine]) => {
        if (!cancelled) setChatAccount(list?.ok && mine?.ok && list.accounts.length >= 2 ? mine.label : "");
      },
      () => {
        if (!cancelled) setChatAccount("");
      },
    );
    return () => {
      cancelled = true;
    };
  }, [convId, chipProvider, usageTick]);

  useEffect(() => {
    if (!isOpen || !usageProvider) return;
    const api = getApi();
    if (!api?.get_gateway_usage) return;
    const refresh = usageForceRef.current;
    usageForceRef.current = false;
    const cached = usageByProviderRef.current[usageCacheKey];
    if (cached) {
      setUsageWindows(cached.windows);
      setUsageNotice(cached.notice);
      setUsageSwitch(cached.switch);
      setUsageBusy(false);
      // Reopening shows the last bars at once and asks again once they are a minute old.
      if (!refresh && Date.now() - cached.at < USAGE_RECHECK_MS) return;
    }
    let cancelled = false;
    if (!cached) setUsageBusy(true);
    void api.get_gateway_usage(usageProvider, "", refresh).then(
      (res) => {
        if (cancelled) return;
        const windows = Array.isArray(res?.windows) ? (res.windows as UsageWindow[]) : [];
        const noticeRaw = res?.notice;
        const notice =
          noticeRaw && typeof noticeRaw.message === "string" && noticeRaw.message.trim()
            ? (noticeRaw as UsageNotice)
            : null;
        const nextWindows = windows.length ? windows : cached?.windows ?? [];
        const nextNotice = nextWindows.length ? null : (notice ?? cached?.notice ?? null);
        const sw = res?.switch as UsageSwitch | undefined;
        const nextSwitch = sw && Array.isArray(sw.options) && sw.options.length >= 2 ? sw : null;
        const apply = (w: UsageWindow[], n: UsageNotice | null, s: UsageSwitch | null) => {
          usageByProviderRef.current[usageCacheKey] = { windows: w, notice: n, switch: s, at: Date.now() };
          setUsageWindows(w);
          setUsageNotice(n);
          setUsageSwitch(s);
          setUsageBusy(false);
        };
        // get_gateway_usage answers for the default account; a chat on another one shows its own.
        const chatProvider =
          nextSwitch && !nextSwitch.plugin_id && convId && api.get_chat_account
            ? nextSwitch.provider || usageProvider
            : "";
        if (!chatProvider || !nextSwitch) return apply(nextWindows, nextNotice, nextSwitch);
        void (async () => {
          const mine = await api.get_chat_account!(convId!, chatProvider).catch(() => null);
          if (cancelled) return;
          if (!mine?.ok || !mine.account || mine.account === nextSwitch.value) {
            return apply(nextWindows, nextNotice, nextSwitch);
          }
          const own = await api.gateway_account_usage?.(chatProvider, mine.account, refresh).catch(() => null);
          if (cancelled) return;
          const ownWindows = own?.ok && Array.isArray(own.windows) ? (own.windows as UsageWindow[]) : [];
          apply(ownWindows, null, { ...nextSwitch, value: mine.account });
        })();
      },
      () => {
        if (!cancelled) setUsageBusy(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [isOpen, usageProvider, usageCacheKey, convId, usageTick]);

  const onUsageAction = () => {
    const action = (usageNotice?.action || "retry").toLowerCase();
    if (action === "login") {
      void openCodingAgentLoginUi({
        providerId: usageNotice?.provider_id || (activeGateway || selectedGateway)?.providerKey || usageProvider,
        title: "Log in to see plan limits",
        body: usageNotice?.message || "",
      });
      return;
    }
    usageForceRef.current = true;
    setUsageTick((n) => n + 1);
  };

  const onUsageSwitch = (id: string) => {
    const api = getApi();
    if (!usageSwitch || !api) return;
    const provider = usageSwitch.provider || usageProvider;
    // In a chat core's switch moves only this chat; outside one (Settings) it sets the default.
    const call = usageSwitch.plugin_id
      ? api.plugin_call?.(usageSwitch.plugin_id, usageSwitch.rpc, { account: id })
      : convId && api.set_chat_account
        ? api.set_chat_account(convId, provider, id)
        : api.set_gateway_account?.(provider, id);
    if (!call) return;
    setUsageBusy(true);
    setUsageSwitch({ ...usageSwitch, value: id });
    void Promise.resolve(call).finally(() => {
      // The other account's bars, never the last account's cached ones.
      delete usageByProviderRef.current[usageCacheKey];
      setUsageWindows([]);
      usageForceRef.current = true;
      setUsageTick((n) => n + 1);
    });
  };

  const usageFooter = (
    <GatewayUsageFooter
      windows={usageWindows}
      notice={usageNotice}
      busy={usageBusy}
      onAction={onUsageAction}
      accountSwitch={usageSwitch}
      onSwitch={onUsageSwitch}
    />
  );
  const slotGateway = activeGateway || selectedGateway;

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

  const onOpenChangeRef = useRef(onOpenChange);
  onOpenChangeRef.current = onOpenChange;
  const closeNow = useCallback(() => {
    if (openRef.current) onOpenChangeRef.current?.(false);
    openRef.current = false;
    setIsOpen(false);
    setNav(null);
    setSearch("");
    setOpenVendor(null);
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

  const pickerExtras = (
    <>
      <LlmPluginSlot
        surface="picker"
        open={isOpen && canOpen}
        providerId={slotGateway?.id || ""}
        pluginId={slotGateway?.pluginId || ""}
        model={normalizedSelectedModel}
      />
      {/* Only where a move is kept (a chat, or a caller that saves it): settings pickers like
          Default Model showed a live slider that snapped back on every drag. */}
      {slotGateway?.showsThinkingEffort !== false && (onEffortChange || convId) ? (
        <ThinkingEffortFooter
          menu={selectedMenu}
          modelName={currentModelData?.name || ""}
          effort={thinkingEffort}
          onChange={persistEffort}
        />
      ) : null}
    </>
  );

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
    onOpenChangeRef.current?.(true);
    histCountRef.current = 0;
    pushHist();
    if (!agents.length) void loadAgents();
    if (!catalogRows && !isModelsCatalogReady()) void loadModels();
  };

  const openDropdownRef = useRef(openDropdown);
  openDropdownRef.current = openDropdown;
  const lastOpenSignal = useRef(openSignal);
  useEffect(() => {
    if (openSignal === lastOpenSignal.current) return;
    lastOpenSignal.current = openSignal;
    openDropdownRef.current();
  }, [openSignal]);

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
    if (!isOpen || !query) return;
    firstHitRef.current?.scrollIntoView({ block: "nearest" });
  }, [isOpen, query, viewApiRows, navGateway, openVendor, gatewaySearchHits]);

  const pickModel = useCallback(
    (row: CatalogModelRow, agentId: string, providerKey?: string) => {
      if (agentIsUnavailable(agentId) && agentId !== "ducky") return;
      const api = getApi();
      setCodingAgent?.(agentId);
      setSelectedModel(row.id);
      const nextMenu = catalogThinkingMenu(row);
      persistEffort(effortInMenu(nextMenu, thinkingEffort));
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
    },
    [
      codingAgent,
      convId,
      persistEffort,
      preserveSelection,
      setSelectedModel,
      setCodingAgent,
      agentIsUnavailable,
      thinkingEffort,
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
          if (disabled) return;
          pickModel(m, opts.agentId, m.providerKey);
          if (closeOnPick) requestClose();
        }}
      >
        <span className="model-selector-option-name" title={m.name}>
          {m.name}
          {m.contextLimit > 0 ? (
            <span
              className="model-selector-ctx"
              title={`Context window: ${m.contextLimit.toLocaleString()} tokens`}
            >
              {formatContext(m.contextLimit)}
            </span>
          ) : null}
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
          {m.note ? (
            <span className="model-selector-unavailable-tag" title={m.note}>
              {m.note}
            </span>
          ) : m.supportsTools ? (
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

  const renderFlatOrVendor = (
    rows: CatalogModelRow[],
    agentId: string,
    selectedId: string | null,
    opts?: { hideEmpty?: boolean },
  ) => {
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
        {rows.length === 0 && !opts?.hideEmpty && (
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
    const catalogPending = !catalogRows && !isModelsCatalogReady();
    const apiHasPick = !!apiSelected && viewApiRows.some((m) => m.id === apiSelected);
    const agentSection = (agent: CodingAgentDto) => {
      const rows = agentModels(agent.id);
      const filtered = query ? rows.filter((m) => m.name.toLowerCase().includes(query)) : rows;
      const unavailable = agentIsUnavailable(agent.id);
      const hasPick = codingAgent === agent.id && filtered.some((m) => m.id === normalizedSelectedModel);
      return (
        <ModelSection
          key={agent.id}
          storageKey={`${gw.id}:${agent.id}`}
          label={agent.label}
          forceOpen={!!query}
          holdsPick={hasPick}
          disabled={unavailable}
          meta={
            agentsChecking ? (
              <span className="model-selector-unavailable-tag">Checking…</span>
            ) : unavailable ? (
              <span className="model-selector-unavailable-tag">Unavailable</span>
            ) : (
              <span className="model-selector-provider-count">{filtered.length}</span>
            )
          }
        >
          {filtered.map((m) =>
            renderRow(m, {
              agentId: agent.id,
              selected: codingAgent === agent.id && m.id === normalizedSelectedModel,
              disabled: unavailable,
            }),
          )}
          {filtered.length === 0 && !unavailable && !agentsChecking ? (
            <div className="model-selector-empty">No models.</div>
          ) : null}
        </ModelSection>
      );
    };
    const readyAgents = gw.nestedAgents.filter((a) => !agentIsUnavailable(a.id));
    const otherAgents = gw.nestedAgents.filter((a) => agentIsUnavailable(a.id));
    // Logged-in coding agents (Claude Code, Codex…), then "API", then agents that are not
    // set up: every one an accordion the person can fold (an API list holds dozens of
    // models), remembered per gateway. A search keeps every section open; the picked model
    // never stops its section folding.
    return (
      <>
        {readyAgents.map(agentSection)}
        {(viewApiRows.length > 0 || catalogPending || gw.nestedAgents.length === 0) && (
          <ModelSection
            storageKey={`${gw.id}:api`}
            label="API"
            forceOpen={!!query}
            holdsPick={apiHasPick}
            meta={
              catalogPending && viewApiRows.length === 0 ? (
                <span className="model-selector-unavailable-tag">Loading…</span>
              ) : (
                <span className="model-selector-provider-count">{viewApiRows.length}</span>
              )
            }
          >
            {catalogPending && viewApiRows.length === 0 ? (
              <div className="model-selector-empty">Loading models…</div>
            ) : (
              renderFlatOrVendor(viewApiRows, apiAgentId, apiSelected)
            )}
          </ModelSection>
        )}
        {otherAgents.map(agentSection)}
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
        // A phone would pop its keyboard over the list; there a tap on the box starts a search.
        autoFocus={!skipAutoFocus()}
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
                {count > 0 ? (
                  <span className="model-selector-provider-count">{count}</span>
                ) : !catalogRows && !isModelsCatalogReady() ? (
                  <span className="model-selector-unavailable-tag">Loading…</span>
                ) : null}
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
    <div className="ui-relative model-selector">
      <button
        ref={triggerRef}
        type="button"
        className={`no-drag model-selector-btn ${labeled ? "model-selector-btn--labeled" : "model-selector-btn--icon"}${isOpen ? " is-open" : ""}`}
        style={{ ["--effort-glow" as string]: String(glow) }}
        onClick={() => (isOpen ? requestClose() : openDropdown())}
        disabled={!canOpen}
        title={triggerLabel}
        aria-label={labeled ? `Model: ${triggerLabel}` : triggerLabel}
      >
        {/* Thinking effort as a small fire flickering behind the logo: none when off, bigger and redder up to the top level. */}
        {!labeled && glow > 0 ? (
          <span className="model-selector-btn-fire" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        ) : null}
        {selectedGateway?.iconDataUrl ? (
          <img
            className="model-selector-btn-logo"
            src={selectedGateway.iconDataUrl}
            alt=""
            draggable={false}
          />
        ) : (
          <Icons.Duck />
        )}
        <BillingBadge kind={billing} corner />
        {labeled ? (
          <>
            <span className="model-selector-btn-text">
              <span className="model-selector-btn-name">{selectedModel ? triggerLabel : placeholder}</span>
              <span className="model-selector-btn-sub">
                {selectedModel ? selectedGateway?.label || "Model" : "Click to pick a model"}
              </span>
            </span>
            <span className="model-selector-btn-caret" aria-hidden="true">
              <Icons.ChevronDown />
            </span>
          </>
        ) : null}
        {chatAccount ? (
          <span className="model-selector-account-chip" title={`${triggerLabel} · ${chatAccount}`}>
            {chatAccount}
          </span>
        ) : null}
      </button>

      <DropdownPanel
        anchorRef={anchorRef}
        open={isOpen && canOpen}
        onClose={requestClose}
        placement={menuPlacement}
        minWidth={300}
        width={320}
        clip
        fill
        idealHeight={560}
        zIndex={menuZIndex}
      >
        {twoLevel ? (
          <div ref={menuTargetRef} className="model-selector-nav" data-level={navGateway ? 1 : 0}>
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
              {navGateway && activeGateway ? (
                <SettingsGear
                  label={activeGateway.label}
                  onOpen={() => {
                    requestClose();
                    openLlmsProviderSettings(activeGateway.id);
                  }}
                />
              ) : (
                <SettingsGear
                  label="Default Model"
                  onOpen={() => {
                    requestClose();
                    openDefaultModelSettings();
                  }}
                />
              )}
            </div>
            {searchBox()}
            <div className="model-selector-viewport">
              <div className="model-selector-track">
                <div className="model-selector-page">
                  <div className="model-selector-scroll">{renderGatewayList()}</div>
                </div>
                <div className="model-selector-page">
                  <div className="model-selector-scroll">
                    {activeGateway ? renderGatewayDetail(activeGateway) : null}
                  </div>
                </div>
              </div>
            </div>
            {pickerExtras}
            {usageFooter}
          </div>
        ) : (
          <>
            {searchBox()}
            <div ref={menuTargetRef} className="model-selector-scroll">
              {renderFlatOrVendor(flatRows, singleAgent, normalizedSelectedModel)}
            </div>
            {pickerExtras}
            {usageFooter}
          </>
        )}
      </DropdownPanel>
    </div>
  );
}
