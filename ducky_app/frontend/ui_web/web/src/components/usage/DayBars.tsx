import type { ProviderUsageDay } from "../../types/panel";
import { fmtCompactTokens } from "../../utils/contextFormat";

/** Per-day stacked input/output bars for the last N days. */
export function DayBars({
  days,
  selectedDate,
  onDayClick,
}: {
  days: ProviderUsageDay[];
  selectedDate?: string | null;
  onDayClick?: (day: ProviderUsageDay) => void;
}) {
  const max = Math.max(
    1,
    ...days.map((d) => Math.max(0, d.input_tokens) + Math.max(0, d.output_tokens)),
  );
  const clickable = !!onDayClick;

  return (
    <div className="usage-day-bars" role="img" aria-label="Tokens per day">
      {days.map((d) => {
        const inp = Math.max(0, d.input_tokens);
        const out = Math.max(0, d.output_tokens);
        const total = inp + out;
        const h = Math.round((total / max) * 100);
        const inpPct = total > 0 ? (inp / total) * 100 : 0;
        const label = d.date.slice(5); // MM-DD
        const selected = selectedDate === d.date;
        const className = `usage-day-bar-col${clickable ? " is-clickable" : ""}${selected ? " is-selected" : ""}`;
        const title = `${d.date}: ${fmtCompactTokens(total)} tokens`;
        const body = (
          <>
            <div className="usage-day-bar-track">
              <div className="usage-day-bar-stack" style={{ height: `${h}%` }}>
                <div className="usage-day-bar-seg usage-day-bar-seg--out" style={{ flex: out || 0.0001 }} />
                <div className="usage-day-bar-seg usage-day-bar-seg--in" style={{ flex: inp || 0.0001 }} />
              </div>
            </div>
            <div className="usage-day-bar-label">{label}</div>
            <div className="usage-day-bar-value" style={{ opacity: total ? 1 : 0.35 }}>
              {total ? fmtCompactTokens(total) : "—"}
            </div>
            <span className="sr-only">
              {d.date}: sent {inpPct.toFixed(0)}% of day total, {fmtCompactTokens(inp)} in /{" "}
              {fmtCompactTokens(out)} out
            </span>
          </>
        );
        if (clickable) {
          return (
            <button
              key={d.date}
              type="button"
              className={className}
              title={title}
              onClick={() => onDayClick?.(d)}
            >
              {body}
            </button>
          );
        }
        return (
          <div key={d.date} className={className} title={title}>
            {body}
          </div>
        );
      })}
    </div>
  );
}
