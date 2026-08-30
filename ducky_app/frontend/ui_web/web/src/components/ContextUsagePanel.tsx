import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useConfirmModal } from "../contexts/ConfirmModalContext";
import { Modal } from "./Modal";
import { getApi } from "../hooks/usePanelApi";
import { Icons } from "../icons/Icons";
import type {
  AgentMode,
  CodingAgentInfo,
  ContextBreakdownItem,
  ContextUsage,
  SessionFile,
  TokenUsageCall,
} from "../types/panel";
import { FileTypeIcon } from "../verse-editor/components/FileTypeIcon";
import { basename } from "../verse-editor/utils/isVerseFile";
import { ScopedCss, useScopedClass } from "../utils/scopedCss";
import { fmtCompactTokens, fmtCostUsd, fmtPercent, fmtTokens } from "../utils/contextFormat";

interface ContextUsagePanelProps {
  convId: string;
  usage: ContextUsage;
  sessionFiles: SessionFile[];
  omitted?: string[];
  agentMode?: AgentMode;
  model?: string;
  agentRunning?: boolean;
  onClose: () => void;
  onOpenFile?: (path: string, name: string) => void;
  onContextChanged?: () => void;
  onClearDraft?: () => void;
}

const STATIC_OMIT_IDS = new Set(["system", "personality", "mcp_tools", "rules", "skill"]);

const RESET_CONFIRM: Record<string, { message: string; confirmLabel: string }> = {
  system: {
    message:
      "Exclude the system prompt from this ducky? Runtime context and project memory will not be sent until restored.",
    confirmLabel: "Exclude",
  },
  personality: {
    message:
      "Exclude this ducky's personality from context? Replies will use the default tone until restored.",
    confirmLabel: "Exclude",
  },
  mcp_tools: {
    message:
      "Exclude MCP tools from this ducky? Tool definitions and server instructions won't be sent until restored.",
    confirmLabel: "Exclude",
  },
  rules: {
    message: "Exclude rules from this ducky? Editing guidance will not be sent until restored.",
    confirmLabel: "Exclude",
  },
  skill: {
    message: "Exclude skills from this ducky and clear the skill snapshot? UEFN wiring guidance won't be sent until restored.",
    confirmLabel: "Exclude",
  },
  conversation: {
    message:
      "Delete all messages in this ducky? Also starts a fresh coding-agent session (recovers stuck Claude/Cursor resumes). This cannot be undone.",
    confirmLabel: "Delete messages",
  },
  summarized: {
    message:
      "Delete all messages in this ducky? Also starts a fresh coding-agent session (recovers stuck Claude/Cursor resumes). This cannot be undone.",
    confirmLabel: "Delete messages",
  },
  draft: {
    message: "Clear the unsent text in the composer?",
    confirmLabel: "Clear draft",
  },
  all: {
    message:
      "Reset all context for this ducky? Messages will be deleted, the coding-agent session restarted, and system segments excluded until restored. This cannot be undone for conversation history.",
    confirmLabel: "Reset all",
  },
};

function normalizeBreakdown(breakdown: ContextBreakdownItem[]): ContextBreakdownItem[] {
  if (!breakdown.length) return breakdown;
  const hasMcpTools = breakdown.some((item) => item.id === "mcp_tools");
  const tools = breakdown.find((item) => item.id === "tools");
  const mcp = breakdown.find((item) => item.id === "mcp");
  if (hasMcpTools || (!tools && !mcp)) return breakdown;

  const rest = breakdown.filter((item) => item.id !== "tools" && item.id !== "mcp");
  const order = [
    "system",
    "personality",
    "mcp_tools",
    "rules",
    "skill",
    "summarized",
    "conversation",
    "agent_internals",
    "draft",
  ];
  const combined: ContextBreakdownItem = {
    id: "mcp_tools",
    label: "MCP Tools",
    color: tools?.color ?? mcp?.color ?? "#a78bfa",
    tokens: (tools?.tokens ?? 0) + (mcp?.tokens ?? 0),
    items: [...(mcp?.items ?? []), ...(tools?.items ?? [])],
  };
  const merged = [...rest, combined];
  merged.sort((a, b) => {
    const ai = order.indexOf(a.id);
    const bi = order.indexOf(b.id);
    return (ai < 0 ? 999 : ai) - (bi < 0 ? 999 : bi);
  });
  return merged;
}

function resetSegmentForId(id: string): string {
  if (id === "summarized") return "conversation";
  return id;
}

/** Stable key for a breakdown row (leaf item) or one of its sub-items. */
function contentKey(itemId: string, subLabel?: string): string {
  return subLabel != null ? `${itemId}::${subLabel}` : itemId;
}

/** Index the exact-text payload from a content-rich fetch by row/sub-item. */
function buildContentMap(breakdown: ContextBreakdownItem[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const item of breakdown) {
    if (item.content) map.set(contentKey(item.id), item.content);
    for (const sub of item.items ?? []) {
      if (sub.content) map.set(contentKey(item.id, sub.label), sub.content);
    }
  }
  return map;
}

function isRowOmitted(id: string, omitted: string[]): boolean {
  if (id === "summarized" || id === "conversation") return false;
  if (id === "mcp_tools") {
    return omitted.includes("mcp_tools") || omitted.includes("tools") || omitted.includes("mcp");
  }
  return omitted.includes(id);
}

function AccordionSection({
  expanded,
  onToggle,
  title,
  summary,
  children,
  className,
}: {
  expanded: boolean;
  onToggle: () => void;
  title: ReactNode;
  summary?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`context-usage-panel-accordion-section${className ? ` ${className}` : ""}`}>
      <button type="button" onClick={onToggle} className="context-usage-panel-accordion-toggle">
        <span
          className={`context-usage-panel-accordion-chevron${expanded ? " context-usage-panel-accordion-chevron--expanded" : ""}`}
        >
          <Icons.ChevronDown />
        </span>
        <span className="context-usage-panel-accordion-title">{title}</span>
        {summary != null ? <span className="context-usage-panel-accordion-summary">{summary}</span> : null}
      </button>
      {expanded ? <div className="context-usage-panel-accordion-body">{children}</div> : null}
    </section>
  );
}

function BarSegment({ widthPct, color, title }: { widthPct: number; color: string; title: string }) {
  const scopeClass = useScopedClass("context-usage-bar-segment");
  return (
    <>
      <ScopedCss
        selector={`.${scopeClass}`}
        rules={{
          width: `${widthPct}%`,
          "min-width": widthPct > 0 ? "2px" : "0",
          background: color,
        }}
      />
      <div className={`context-usage-panel-bar-segment ${scopeClass}`} title={title} />
    </>
  );
}

export function ContextUsagePanel({
  convId,
  usage,
  sessionFiles,
  omitted = [],
  agentMode = "agent",
  model = "",
  agentRunning = false,
  onClose,
  onOpenFile,
  onContextChanged,
  onClearDraft,
}: ContextUsagePanelProps) {
  const { confirm } = useConfirmModal();
  const [usageExpanded, setUsageExpanded] = useState(true);
  const [apiExpanded, setApiExpanded] = useState(false);
  const [filesExpanded, setFilesExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [contentMap, setContentMap] = useState<Map<string, string>>(() => new Map());
  const [viewer, setViewer] = useState<{ title: string; content: string } | null>(null);
  const breakdown = normalizeBreakdown(usage.breakdown ?? []);
  const calls = usage.calls ?? [];
  const sentTotal = Math.max(0, usage.input_tokens);
  const receivedTotal = Math.max(0, usage.output_tokens);
  const apiTotal = Math.max(0, usage.total_tokens ?? sentTotal + receivedTotal);
  const cacheReadTotal = Math.max(0, usage.total_cache_read ?? 0);
  const cacheWriteTotal = Math.max(0, usage.total_cache_write ?? 0);
  const lastCall = calls.length ? calls[calls.length - 1] : undefined;
  const lastTurnCache =
    lastCall == null
      ? ""
      : (lastCall.cache_read_tokens ?? 0) > 0
        ? `${fmtTokens(lastCall.cache_read_tokens ?? 0)} cached`
        : (lastCall.cache_write_tokens ?? 0) > 0
          ? "cache write"
          : "cache miss";
  const cacheHitRate = usage.cache_hit_rate ?? 0;
  const cacheHitRateCumulative = usage.cache_hit_rate_cumulative ?? 0;
  const callCount = Math.max(0, usage.call_count ?? calls.length);
  const costUsd = typeof usage.cost_usd === "number" ? usage.cost_usd : null;
  const used = Math.max(0, usage.used_tokens);
  const limit = Math.max(1, usage.context_limit);
  const pct = fmtPercent(used, limit);
  const actionsDisabled = agentRunning || busy;
  const agentInfo = usage.agent_info;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // While the segment viewer is open, Escape closes only the viewer (its own
      // focus trap handles that). Leave the panel up — close one layer at a time.
      if (e.key === "Escape" && !viewer) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, viewer]);

  // The live `usage` prop is refreshed on every keystroke and stays lean (no
  // segment text). Fetch the heavier content-rich breakdown once here so rows
  // can be opened to reveal the exact text — refetching only when the segments
  // themselves change (excluded/restored), never on typing.
  const omittedKey = omitted.join(",");
  useEffect(() => {
    let cancelled = false;
    const api = getApi();
    if (!api) return;
    void api
      .get_context_usage(convId, model, agentMode, "", true)
      .then((rich) => {
        if (!cancelled) setContentMap(buildContentMap(rich.breakdown ?? []));
      })
      .catch(() => {
        if (!cancelled) setContentMap(new Map());
      });
    return () => {
      cancelled = true;
    };
  }, [convId, model, agentMode, omittedKey]);

  const runReset = useCallback(
    async (segmentId: string) => {
      const opts = RESET_CONFIRM[segmentId];
      if (!opts) return;
      if (!(await confirm({ ...opts, danger: true }))) return;

      if (segmentId === "draft") {
        onClearDraft?.();
        onContextChanged?.();
        return;
      }

      const api = getApi();
      if (!api?.reset_context) return;
      setBusy(true);
      try {
        await api.reset_context(convId, [resetSegmentForId(segmentId)], agentMode, model);
        if (segmentId === "all") onClearDraft?.();
        onContextChanged?.();
      } finally {
        setBusy(false);
      }
    },
    [agentMode, confirm, convId, model, onClearDraft, onContextChanged],
  );

  const runRestore = useCallback(
    async (segmentId: string) => {
      if (!STATIC_OMIT_IDS.has(segmentId)) return;
      if (
        !(await confirm({
          message: `Restore ${segmentId} context for this ducky?`,
          confirmLabel: "Restore",
        }))
      ) {
        return;
      }
      const api = getApi();
      if (!api?.restore_context) return;
      setBusy(true);
      try {
        await api.restore_context(convId, [segmentId], agentMode, model);
        onContextChanged?.();
      } finally {
        setBusy(false);
      }
    },
    [agentMode, confirm, convId, model, onContextChanged],
  );

  return (
    <>
      <div className="context-usage-panel-usage-header">
        <span className="context-usage-panel-usage-title">Context Usage</span>
        <button type="button" onClick={onClose} aria-label="Close" className="context-usage-panel-close-btn">
          <Icons.Close />
        </button>
      </div>

      {agentInfo ? <AgentInfoSection info={agentInfo} /> : null}

      <AccordionSection
        expanded={usageExpanded}
        onToggle={() => setUsageExpanded((v) => !v)}
        title={<span className="context-usage-panel-stats-pct">{pct}% Full</span>}
        summary={
          <>
            ~{fmtCompactTokens(used)} / {fmtCompactTokens(limit)} Tokens
          </>
        }
      >
        <p className="context-usage-panel-stats-hint">
          {agentInfo
            ? `${agentInfo.label} session — its real context window last turn. API Tokens below are what ${agentInfo.label} actually sent.`
            : "Next prompt estimate (one snapshot). Expand API Tokens for cumulative usage across model calls."}
        </p>

        <div className="context-usage-panel-bar">
          {breakdown.map((item) => {
            const barTokens = item.gated ? (item.active_tokens ?? 0) : item.tokens;
            const widthPct = used > 0 ? (barTokens / used) * 100 : 0;
            if (widthPct <= 0) return null;
            return (
              <BarSegment
                key={item.id}
                widthPct={widthPct}
                color={item.color}
                title={`${item.label}: ${fmtTokens(barTokens)}`}
              />
            );
          })}
        </div>

        {agentRunning && (
          <div className="context-usage-panel-running-note">Wait for the agent to finish before resetting context.</div>
        )}

        {!agentInfo ? (
          <>
            <div className="context-usage-panel-breakdown-list">
              {breakdown.map((item) => (
                <BreakdownRow
                  key={item.id}
                  item={item}
                  omitted={isRowOmitted(item.id, omitted)}
                  actionsDisabled={actionsDisabled}
                  contentMap={contentMap}
                  onView={(title, content) => setViewer({ title, content })}
                  onReset={() => void runReset(item.id)}
                  onRestore={() => void runRestore(item.id)}
                />
              ))}
            </div>

            <div className="context-usage-panel-reset-footer">
              <button
                type="button"
                className="context-usage-panel-reset-all-btn"
                disabled={actionsDisabled}
                onClick={() => void runReset("all")}
              >
                Reset all context
              </button>
            </div>
          </>
        ) : null}
      </AccordionSection>

      <AccordionSection
        expanded={apiExpanded}
        onToggle={() => setApiExpanded((v) => !v)}
        title="API Tokens"
        summary={
          <span className="context-usage-panel-api-total-value context-usage-panel-api-total-value--sent">
            Sent {fmtTokens(sentTotal)}
          </span>
        }
      >
        <div className="context-usage-panel-api-totals">
          <div className="context-usage-panel-api-total-row">
            <span className="context-usage-panel-api-total-label">Sent (all steps)</span>
            <span className="context-usage-panel-api-total-value context-usage-panel-api-total-value--sent">
              {fmtTokens(sentTotal)}
            </span>
          </div>
          <div className="context-usage-panel-api-total-row">
            <span className="context-usage-panel-api-total-label">Received</span>
            <span className="context-usage-panel-api-total-value context-usage-panel-api-total-value--received">
              {fmtTokens(receivedTotal)}
            </span>
          </div>
          <div className="context-usage-panel-api-total-row context-usage-panel-api-total-row--sum">
            <span className="context-usage-panel-api-total-label">Total</span>
            <span className="context-usage-panel-api-total-value">{fmtTokens(apiTotal)}</span>
          </div>
          <div className="context-usage-panel-api-total-row">
            <span className="context-usage-panel-api-total-label">Cached reads</span>
            <span className="context-usage-panel-api-total-value context-usage-panel-api-total-value--cached">
              {fmtTokens(cacheReadTotal)}
            </span>
          </div>
          {lastTurnCache ? (
            <div className="context-usage-panel-api-total-row">
              <span className="context-usage-panel-api-total-label">Last turn</span>
              <span className="context-usage-panel-api-total-value">{lastTurnCache}</span>
            </div>
          ) : null}
          <div className="context-usage-panel-api-total-row">
            <span className="context-usage-panel-api-total-label">Cache writes</span>
            <span className="context-usage-panel-api-total-value">{fmtTokens(cacheWriteTotal)}</span>
          </div>
          {costUsd != null && (
            <div className="context-usage-panel-api-total-row context-usage-panel-api-total-row--sum">
              <span className="context-usage-panel-api-total-label">Est. cost</span>
              <span
                className="context-usage-panel-api-total-value context-usage-panel-api-total-value--cost"
                title="Estimate from public per-token pricing, including cache discounts"
              >
                {fmtCostUsd(costUsd)}
              </span>
            </div>
          )}
          <div className="context-usage-panel-api-meta">
            {callCount} API call{callCount === 1 ? "" : "s"}
            {cacheHitRate > 0 ? ` · ${cacheHitRate}% cache hit (last call)` : ""}
            {cacheHitRateCumulative > 0 ? ` · ${cacheHitRateCumulative}% overall` : ""}
            {agentRunning ? " · updating live" : ""}
          </div>
          <p className="context-usage-panel-api-hint">
            {callCount} model call{callCount === 1 ? "" : "s"} in this chat — each step re-sends the prompt, so
            Sent adds up (e.g. 6 steps × ~10K ≈ 60K). Context meter above is one snapshot (~16K). Cached prefix
            tokens are billed at a discount.
          </p>
        </div>

        {calls.length > 0 ? (
          <div className="context-usage-panel-api-calls">
            {[...calls].reverse().map((call, index) => (
              <ApiCallRow key={`${call.ts}-${call.step}-${index}`} call={call} index={calls.length - index} />
            ))}
          </div>
        ) : (
          <div className="context-usage-panel-api-empty">No API calls logged yet for this ducky.</div>
        )}
      </AccordionSection>

      <AccordionSection
        expanded={filesExpanded}
        onToggle={() => setFilesExpanded((v) => !v)}
        title={
          <>
            {sessionFiles.length} File{sessionFiles.length === 1 ? "" : "s"}
          </>
        }
      >
        {sessionFiles.length > 0 ? (
          <div className="context-usage-panel-files-list">
            {sessionFiles.map((file) => (
              <button
                key={file.path}
                type="button"
                onClick={() => onOpenFile?.(file.path, basename(file.path))}
                className={`context-usage-panel-file-btn${onOpenFile ? " context-usage-panel-file-btn--clickable" : ""}`}
              >
                <span className="context-usage-panel-file-icon">
                  <FileTypeIcon path={file.path} size={13} />
                </span>
                <span className="context-usage-panel-file-name">{basename(file.path)}</span>
                {file.lines_added != null && file.lines_added > 0 && (
                  <span className="context-usage-panel-file-added">+{file.lines_added}</span>
                )}
              </button>
            ))}
          </div>
        ) : (
          <div className="context-usage-panel-files-empty">No files edited in this chat yet.</div>
        )}
      </AccordionSection>

      {viewer ? (
        <Modal open onClose={() => setViewer(null)} title={viewer.title} width={640} zIndex={100020}>
          <pre className="context-usage-panel-content-view">{viewer.content}</pre>
        </Modal>
      ) : null}
    </>
  );
}

function AgentInfoSection({ info }: { info: CodingAgentInfo }) {
  const loginState =
    info.logged_in === true
      ? { label: "Ready", cls: "is-ok" }
      : info.logged_in === false
        ? { label: "Not signed in — chat will prompt", cls: "is-warn" }
        : null;
  const sessionLabel = info.session_active
    ? "Live — remembers this chat"
    : info.has_run
      ? "New session next turn"
      : "Not started yet";
  return (
    <section className="context-usage-panel-agent">
      <div className="context-usage-panel-agent-head">
        <span className="context-usage-panel-agent-badge">Coding agent</span>
        <span className="context-usage-panel-agent-name">{info.label}</span>
        {loginState ? (
          <span className={`context-usage-panel-agent-login ${loginState.cls}`}>{loginState.label}</span>
        ) : null}
      </div>
      <div className="context-usage-panel-agent-rows">
        <div className="context-usage-panel-agent-row">
          <span className="context-usage-panel-agent-row-label">Model</span>
          <span className="context-usage-panel-agent-row-value">{info.model || "—"}</span>
        </div>
        <div className="context-usage-panel-agent-row">
          <span className="context-usage-panel-agent-row-label">Session</span>
          <span className="context-usage-panel-agent-row-value">{sessionLabel}</span>
        </div>
        {info.num_turns > 0 ? (
          <div className="context-usage-panel-agent-row">
            <span className="context-usage-panel-agent-row-label">Agent turns (last run)</span>
            <span className="context-usage-panel-agent-row-value">{info.num_turns}</span>
          </div>
        ) : null}
        {info.permission_mode ? (
          <div className="context-usage-panel-agent-row">
            <span className="context-usage-panel-agent-row-label">Permission mode</span>
            <span className="context-usage-panel-agent-row-value">{info.permission_mode}</span>
          </div>
        ) : null}
        {info.skills !== undefined ? (
          <div className="context-usage-panel-agent-row">
            <span className="context-usage-panel-agent-row-label">UEFN skills</span>
            <span className="context-usage-panel-agent-row-value">
              {info.skills.length > 0 ? info.skills.join(", ") : "not deployed — Settings → LLMs → provider → Apply"}
            </span>
          </div>
        ) : null}
      </div>
      {info.status && info.available === false ? (
        <p className="context-usage-panel-agent-status">{info.status}</p>
      ) : null}
    </section>
  );
}

const CACHE_MODE_LABELS: Record<"cached" | "implicit" | "local", { label: string; title: string }> = {
  cached: { label: "cached", title: "Frozen prefix — provider cache breakpoint (Anthropic/OpenAI) discounts repeat sends" },
  implicit: { label: "implicit", title: "Gemini's automatic prefix caching — stable ordering makes repeat sends cheaper without an explicit cache API" },
  local: { label: "local", title: "Local gateway keeps the model + its KV cache warm between turns (keep_alive) instead of server-side caching" },
};

function CacheModeBadge({ mode }: { mode: "cached" | "implicit" | "local" }) {
  const info = CACHE_MODE_LABELS[mode];
  return (
    <span className={`context-usage-panel-breakdown-cache-badge context-usage-panel-breakdown-cache-badge--${mode}`} title={info.title}>
      {info.label}
    </span>
  );
}

function ApiCallRow({ call, index }: { call: TokenUsageCall; index: number }) {
  const when = call.ts > 0 ? new Date(call.ts * 1000).toLocaleTimeString() : "";
  const stepLabel = call.step > 0 ? `step ${call.step}` : `#${index}`;
  const cached = call.cache_read_tokens ?? 0;
  const cost = typeof call.cost_usd === "number" ? call.cost_usd : null;
  return (
    <div className="context-usage-panel-api-call-row">
      <span className="context-usage-panel-api-call-step">{stepLabel}</span>
      <span className="context-usage-panel-api-call-sent">
        ↑{fmtTokens(call.input_tokens)}
        {cached > 0 ? ` (${fmtTokens(cached)} cached)` : ""}
      </span>
      <span className="context-usage-panel-api-call-received">↓{fmtTokens(call.output_tokens)}</span>
      {cost != null ? <span className="context-usage-panel-api-call-cost">{fmtCostUsd(cost)}</span> : null}
      {when ? <span className="context-usage-panel-api-call-time">{when}</span> : null}
    </div>
  );
}

function BreakdownRow({
  item,
  omitted,
  actionsDisabled,
  contentMap,
  onView,
  onReset,
  onRestore,
}: {
  item: ContextBreakdownItem;
  omitted: boolean;
  actionsDisabled: boolean;
  contentMap: Map<string, string>;
  onView: (title: string, content: string) => void;
  onReset: () => void;
  onRestore: () => void;
}) {
  const scopeClass = useScopedClass("context-usage-breakdown-dot");
  const [expanded, setExpanded] = useState(false);
  const canReset =
    item.id === "conversation" ||
    item.id === "summarized" ||
    item.id === "draft" ||
    STATIC_OMIT_IDS.has(item.id);
  const showReset = canReset && !omitted && (item.tokens > 0 || item.id === "conversation");
  const showRestore = omitted && STATIC_OMIT_IDS.has(item.id);
  const subItems = omitted ? [] : item.items ?? [];
  const expandable = subItems.length > 0;
  // A leaf row (no sub-items) whose text we can show — e.g. Ducky personality.
  const leafContent = !expandable && !omitted ? contentMap.get(contentKey(item.id)) : undefined;

  const inner = (
    <>
      <span
        className={`context-usage-panel-breakdown-chevron${expandable && expanded ? " context-usage-panel-breakdown-chevron--expanded" : ""}`}
      >
        {expandable ? <Icons.ChevronDown /> : null}
      </span>
      <span className={`context-usage-panel-breakdown-dot ${scopeClass}`} />
      <span className="context-usage-panel-breakdown-label">
        {item.label}
        {item.gated ? (
          <span className="context-usage-panel-breakdown-gated-hint" title="Enabled for this ducky but not sent until the message looks like verse/device work">
            {" "}
            (gated)
          </span>
        ) : null}
        {item.cache_mode ? <CacheModeBadge mode={item.cache_mode} /> : null}
      </span>
      <span className="context-usage-panel-breakdown-tokens">
        {omitted ? "Excluded" : fmtCompactTokens(item.tokens)}
      </span>
    </>
  );

  return (
    <div className="context-usage-panel-breakdown-item">
      <div className={`context-usage-panel-breakdown-row${omitted ? " context-usage-panel-breakdown-row--omitted" : ""}`}>
        <ScopedCss selector={`.${scopeClass}`} rules={{ background: item.color }} />
        {expandable ? (
          <button
            type="button"
            className="context-usage-panel-breakdown-main context-usage-panel-breakdown-main--expandable"
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
          >
            {inner}
          </button>
        ) : leafContent ? (
          <button
            type="button"
            className="context-usage-panel-breakdown-main context-usage-panel-breakdown-main--expandable"
            title="View content"
            onClick={() => onView(item.label, leafContent)}
          >
            {inner}
          </button>
        ) : (
          <div className="context-usage-panel-breakdown-main">{inner}</div>
        )}
        {showRestore ? (
          <button
            type="button"
            className="context-usage-panel-row-restore-btn"
            disabled={actionsDisabled}
            onClick={onRestore}
          >
            Restore
          </button>
        ) : showReset ? (
          <button
            type="button"
            className="context-usage-panel-row-reset-btn"
            disabled={actionsDisabled}
            aria-label={`Reset ${item.label}`}
            onClick={onReset}
          >
            <Icons.Trash />
          </button>
        ) : null}
      </div>
      {expandable && expanded ? (
        <ul className="context-usage-panel-breakdown-sublist">
          {subItems.map((sub, index) => {
            const subContent = contentMap.get(contentKey(item.id, sub.label));
            const body = (
              <>
                <span className="context-usage-panel-breakdown-sublabel" title={sub.sublabel || sub.label}>
                  {sub.label}
                  {sub.sublabel ? (
                    <span className="context-usage-panel-breakdown-subhint">{sub.sublabel}</span>
                  ) : null}
                </span>
                <span className="context-usage-panel-breakdown-subtokens">{fmtCompactTokens(sub.tokens)}</span>
              </>
            );
            return (
              <li key={`${sub.label}-${index}`}>
                {subContent ? (
                  <button
                    type="button"
                    className="context-usage-panel-breakdown-subrow context-usage-panel-breakdown-subrow--clickable"
                    title="View content"
                    onClick={() => onView(sub.label, subContent)}
                  >
                    {body}
                  </button>
                ) : (
                  <div className="context-usage-panel-breakdown-subrow">{body}</div>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
