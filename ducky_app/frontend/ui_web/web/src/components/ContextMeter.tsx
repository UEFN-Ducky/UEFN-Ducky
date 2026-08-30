import { useRef, type RefObject } from "react";
import { DropdownPanel } from "./DropdownPanel";
import { ContextUsagePanel } from "./ContextUsagePanel";
import type { ContextUsage, SessionFile, AgentMode } from "../types/panel";
import { fmtTokens } from "../utils/contextFormat";

interface ContextMeterProps {
  usedTokens: number;
  contextLimit: number;
  inputTokens?: number;
  outputTokens?: number;
  usage?: ContextUsage;
  sessionFiles?: SessionFile[];
  convId?: string;
  omitted?: string[];
  agentMode?: AgentMode;
  model?: string;
  agentRunning?: boolean;
  panelOpen?: boolean;
  /** Composer uses top; subducky banner uses bottom so the panel opens into the chat. */
  panelPlacement?: "top" | "bottom";
  onTogglePanel?: () => void;
  onClosePanel?: () => void;
  onOpenFile?: (path: string, name: string) => void;
  onContextChanged?: () => void;
  onClearDraft?: () => void;
}

function ringTone(ratio: number): "ok" | "warn" | "critical" {
  if (ratio >= 0.92) return "critical";
  if (ratio >= 0.75) return "warn";
  return "ok";
}

export function ContextMeter({
  usedTokens,
  contextLimit,
  panelOpen = false,
  panelPlacement = "top",
  onTogglePanel,
  onClosePanel,
  usage,
  sessionFiles = [],
  convId = "",
  omitted = [],
  agentMode = "agent",
  model = "",
  agentRunning = false,
  onOpenFile,
  onContextChanged,
  onClearDraft,
}: ContextMeterProps) {
  const anchorRef = useRef<HTMLButtonElement>(null);
  const limit = Math.max(1, contextLimit);
  const used = Math.max(0, usedTokens);
  const ratio = Math.min(1, used / limit);
  const size = 18;
  const stroke = 2.5;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - ratio);
  const tone = ringTone(ratio);
  const lastCall = usage?.calls?.length ? usage.calls[usage.calls.length - 1] : undefined;
  const lastRead = lastCall?.cache_read_tokens ?? 0;
  const lastWrite = lastCall?.cache_write_tokens ?? 0;
  const lastIn = lastCall?.input_tokens ?? 0;
  const cacheChip =
    lastRead > 0 ? `${fmtTokens(lastRead)} cached` : lastWrite > 0 ? "cache write" : lastIn > 0 ? "cache miss" : "";

  const reportUsage: ContextUsage = usage ?? {
    used_tokens: used,
    context_limit: limit,
    input_tokens: 0,
    output_tokens: 0,
  };

  return (
    <div className="no-drag context-meter-root">
      <DropdownPanel
        anchorRef={anchorRef as RefObject<HTMLElement | null>}
        open={panelOpen}
        onClose={() => onClosePanel?.()}
        placement={panelPlacement}
        minWidth={360}
        width={380}
      >
        <ContextUsagePanel
          convId={convId}
          usage={reportUsage}
          sessionFiles={sessionFiles}
          omitted={omitted}
          agentMode={agentMode}
          model={model}
          agentRunning={agentRunning}
          onClose={() => onClosePanel?.()}
          onOpenFile={onOpenFile}
          onContextChanged={onContextChanged}
          onClearDraft={onClearDraft}
        />
      </DropdownPanel>

      <button
        ref={anchorRef}
        type="button"
        onClick={() => onTogglePanel?.()}
        aria-label={`Context ${fmtTokens(used)} of ${fmtTokens(limit)} tokens${cacheChip ? ` · ${cacheChip}` : ""}`}
        aria-expanded={panelOpen}
        className="context-meter-btn"
        title={cacheChip || undefined}
      >
        {cacheChip ? <span className="context-meter-cache-chip">{cacheChip}</span> : null}
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="var(--border-light)"
            strokeWidth={stroke}
          />
          <circle
            className={`context-meter-ring-progress context-meter-ring-progress--${tone}`}
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
          />
        </svg>
      </button>
    </div>
  );
}
