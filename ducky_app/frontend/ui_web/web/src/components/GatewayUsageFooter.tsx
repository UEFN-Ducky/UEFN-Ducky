import {
  effortInMenu,
  footerThinkingMenu,
  type ThinkingMenu,
} from "./thinkingMenu";

export type UsageWindow = {
  id: string;
  label: string;
  used: number;
  limit: number;
  unit?: string;
  reset?: string;
  readout?: string;
};

export type UsageNotice = {
  message: string;
  action?: string;
  action_label?: string;
  provider_id?: string;
  agent_id?: string;
};

function windowRows(windows: UsageWindow[] | null | undefined): UsageWindow[] {
  if (!windows?.length) return [];
  return windows.filter((w) => Number(w.limit) > 0);
}

function pctOf(w: UsageWindow): number {
  const limit = Number(w.limit);
  if (!Number.isFinite(limit) || limit <= 0) return 0;
  return Math.max(0, Math.min(100, (Number(w.used) / limit) * 100));
}

function readoutOf(w: UsageWindow): string {
  const text = (w.readout || "").trim();
  if (text) return text;
  return `${Math.round(pctOf(w))}%`;
}

/** Live plan caps under Faster/Smarter. Hidden when the gateway sent none. */
export function GatewayUsageFooter({
  windows,
  notice,
  busy = false,
  onAction,
}: {
  windows: UsageWindow[] | null | undefined;
  notice?: UsageNotice | null;
  busy?: boolean;
  onAction?: () => void;
}) {
  const rows = windowRows(windows);
  const msg = (notice?.message || "").trim();
  if (!rows.length && !msg) return null;
  return (
    <div className="model-selector-usage" onClick={(e) => e.stopPropagation()}>
      {rows.map((w) => {
        const pct = pctOf(w);
        const tone = pct >= 99 ? "is-max" : pct >= 80 ? "is-hot" : "";
        return (
          <div key={w.id || w.label} className={`model-selector-cap ${tone}`.trim()}>
            <div className="model-selector-cap-top">
              <span className="model-selector-cap-label">{w.label}</span>
              {w.reset ? <span className="model-selector-cap-reset">{w.reset}</span> : null}
              <span className="model-selector-cap-pct">{readoutOf(w)}</span>
            </div>
            <div className="model-selector-cap-track" aria-hidden="true">
              <i style={{ width: `${pct}%` }} />
            </div>
          </div>
        );
      })}
      {msg ? (
        <div className="model-selector-cap-notice">
          <span>{msg}</span>
          {notice?.action_label && onAction ? (
            <button type="button" disabled={busy} onClick={onAction}>
              {busy ? "Working…" : notice.action_label}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** 0 = Faster/off, 1 = Smarter. Drives the trigger glow. */
export function effortGlow(menu: ThinkingMenu | null | undefined, effort: string): number {
  const resolved = footerThinkingMenu(menu);
  const levels = resolved.levels;
  const last = Math.max(0, levels.length - 1);
  if (last === 0) return 0;
  const id = effortInMenu(resolved, effort);
  const index = Math.max(
    0,
    levels.findIndex((l) => (l.id || "").toLowerCase() === id),
  );
  return index / last;
}
