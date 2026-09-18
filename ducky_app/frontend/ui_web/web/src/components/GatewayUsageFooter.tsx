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
};

function formatAmt(n: number): string {
  if (!Number.isFinite(n)) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}K`;
  if (n >= 1000) return n.toLocaleString();
  return String(Math.round(n * 10) / 10);
}

function windowRows(windows: UsageWindow[] | null | undefined): UsageWindow[] {
  if (!windows?.length) return [];
  return windows.filter((w) => Number(w.limit) > 0);
}

/** Read-only quota rails under the Faster/Smarter slider. Hidden when empty. */
export function GatewayUsageFooter({ windows }: { windows: UsageWindow[] | null | undefined }) {
  const rows = windowRows(windows);
  if (!rows.length) return null;
  return (
    <div className="model-selector-usage" onClick={(e) => e.stopPropagation()}>
      {rows.map((w) => {
        const limit = Number(w.limit);
        const used = Math.max(0, Math.min(Number(w.used) || 0, limit));
        const pct = limit === 0 ? 0 : used / limit;
        const unit = (w.unit || "").trim();
        const label = `${w.label} ${formatAmt(used)} / ${formatAmt(limit)}${unit ? ` ${unit}` : ""}`;
        return (
          <div key={w.id || w.label} className="model-selector-effort is-disabled">
            <div className="model-selector-effort-readout">{label}</div>
            <div className="model-selector-effort-slider">
              <div className="model-selector-effort-rail" aria-hidden="true">
                <span className="model-selector-effort-dots" />
                <span className="model-selector-effort-thumb" style={{ left: `${pct * 100}%` }} />
              </div>
              <input
                type="range"
                className="model-selector-effort-range"
                min={0}
                max={limit}
                step="any"
                value={used}
                disabled
                aria-label={label}
                readOnly
              />
            </div>
          </div>
        );
      })}
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
