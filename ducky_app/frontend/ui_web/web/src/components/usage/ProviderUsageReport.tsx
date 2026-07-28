import { useCallback, useEffect, useMemo, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import { Icons } from "../../icons/Icons";
import type {
  ProviderUsageAgent,
  ProviderUsageDay,
  ProviderUsageDucky,
  ProviderUsageModel,
  ProviderUsageReport as ProviderUsageReportDto,
} from "../../types/panel";
import { fmtCompactTokens, fmtCostUsd, fmtTokens } from "../../utils/contextFormat";
import { DayBars } from "./DayBars";
import { DonutChart, defaultSliceColors } from "./DonutChart";

export type ProviderUsageReportProps = {
  providerId: string;
  label: string;
  days?: number;
  /** Hide the big title when the host already shows one (settings slide). */
  hideTitle?: boolean;
};

type Dim = "overview" | "models" | "duckies" | "agents" | "days";

type DetailRow = {
  id: string;
  name: string;
  meta: string;
  tokens: number;
  input: number;
  output: number;
  calls: number;
  cost: number;
  cacheRead?: number;
};

function emptyReport(providerId: string, days: number): ProviderUsageReportDto {
  return {
    provider: providerId,
    days,
    call_count: 0,
    total_input: 0,
    total_output: 0,
    total_tokens: 0,
    total_cache_read: 0,
    total_cache_write: 0,
    cache_hit_rate: 0,
    cost_usd: null,
    by_day: [],
    by_model: [],
    by_agent: [],
    by_ducky: [],
  };
}

function rowMeta(calls: number, cost: number): string {
  const bits = [`${fmtTokens(calls)} calls`];
  if (cost > 0) bits.push(fmtCostUsd(cost));
  return bits.join(" · ");
}

function modelRows(models: ProviderUsageModel[]): DetailRow[] {
  return models.map((m) => ({
    id: m.model,
    name: m.model,
    meta: rowMeta(m.call_count, m.cost_usd),
    tokens: m.input_tokens + m.output_tokens,
    input: m.input_tokens,
    output: m.output_tokens,
    calls: m.call_count,
    cost: m.cost_usd,
    cacheRead: m.cache_read_tokens,
  }));
}

function agentRows(agents: ProviderUsageAgent[]): DetailRow[] {
  return agents.map((a) => ({
    id: a.agent,
    name: a.agent === "ducky" ? "Ducky" : a.agent,
    meta: rowMeta(a.call_count, a.cost_usd),
    tokens: a.input_tokens + a.output_tokens,
    input: a.input_tokens,
    output: a.output_tokens,
    calls: a.call_count,
    cost: a.cost_usd,
    cacheRead: a.cache_read_tokens,
  }));
}

function duckyRows(duckies: ProviderUsageDucky[]): DetailRow[] {
  return duckies.map((d) => ({
    id: d.conv_id || d.label,
    name: d.label,
    meta: rowMeta(d.call_count, d.cost_usd),
    tokens: d.input_tokens + d.output_tokens,
    input: d.input_tokens,
    output: d.output_tokens,
    calls: d.call_count,
    cost: d.cost_usd,
    cacheRead: d.cache_read_tokens,
  }));
}

function dayRows(days: ProviderUsageDay[]): DetailRow[] {
  return [...days]
    .filter((d) => d.call_count > 0 || d.input_tokens > 0 || d.output_tokens > 0)
    .reverse()
    .map((d) => ({
      id: d.date,
      name: d.date,
      meta: rowMeta(d.call_count, d.cost_usd),
      tokens: d.input_tokens + d.output_tokens,
      input: d.input_tokens,
      output: d.output_tokens,
      calls: d.call_count,
      cost: d.cost_usd,
      cacheRead: d.cache_read_tokens,
    }));
}

const DIMS: { id: Dim; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "models", label: "Models" },
  { id: "duckies", label: "Duckies" },
  { id: "agents", label: "Agents" },
  { id: "days", label: "Days" },
];

/**
 * Reusable per-provider usage dashboard.
 * Self-fetches via `get_provider_usage` so it can later ship inside an LLM plugin.
 */
export function ProviderUsageReport({
  providerId,
  label,
  days = 7,
  hideTitle = false,
}: ProviderUsageReportProps) {
  const [report, setReport] = useState<ProviderUsageReportDto>(() => emptyReport(providerId, days));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dim, setDim] = useState<Dim>("overview");
  const [openId, setOpenId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const api = getApi();
    if (!api?.get_provider_usage) {
      setError("Usage API unavailable");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await api.get_provider_usage(providerId, days);
      setReport(data);
      if (data.error) setError(data.error);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load usage");
    } finally {
      setLoading(false);
    }
  }, [providerId, days]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    setOpenId(null);
  }, [dim, providerId]);

  const models = report.by_model ?? [];
  const agents = report.by_agent ?? [];
  const duckies = report.by_ducky ?? [];

  const modelSlices = models.slice(0, 8).map((m, i) => ({
    id: m.model,
    label: m.model,
    value: m.input_tokens + m.output_tokens,
    color: defaultSliceColors(8)[i],
  }));
  const ioSlices = [
    { id: "sent", label: "Sent", value: report.total_input, color: "var(--amber)" },
    { id: "received", label: "Received", value: report.total_output, color: "var(--green)" },
  ];
  const agentSlices = agents.slice(0, 8).map((a, i) => ({
    id: a.agent,
    label: a.agent === "ducky" ? "Ducky" : a.agent,
    value: a.input_tokens + a.output_tokens,
    color: defaultSliceColors(8)[i],
  }));

  const detailRows = useMemo(() => {
    if (dim === "models") return modelRows(models);
    if (dim === "agents") return agentRows(agents);
    if (dim === "duckies") return duckyRows(duckies);
    if (dim === "days") return dayRows(report.by_day);
    return [];
  }, [dim, models, agents, duckies, report.by_day]);

  const detailTitle =
    dim === "models"
      ? "Models"
      : dim === "agents"
        ? "Agents"
        : dim === "duckies"
          ? "Duckies"
          : dim === "days"
            ? "Days"
            : "Details";

  const showList = dim !== "overview";

  return (
    <div className="provider-usage-report">
      <div className="provider-usage-report-head">
        <div>
          {hideTitle ? null : <div className="provider-usage-report-title">{label}</div>}
          <div className="provider-usage-report-sub">
            Last {report.days || days} days · {report.call_count.toLocaleString("en-US")} calls
          </div>
        </div>
        <button
          type="button"
          className="settings-btn llms-provider-stats-btn"
          onClick={() => void refresh()}
          disabled={loading}
          title={loading ? "Loading…" : "Refresh"}
          aria-label={loading ? "Loading…" : "Refresh"}
        >
          <Icons.Refresh />
        </button>
      </div>

      {error ? <div className="provider-usage-report-error">{error}</div> : null}

      <div className="usage-metric-strip" role="toolbar" aria-label="Usage metrics">
        <button
          type="button"
          className={`usage-metric${dim === "overview" ? " is-active" : ""}`}
          onClick={() => setDim("overview")}
        >
          <div className="usage-metric-label">Sent</div>
          <div className="usage-metric-value">{fmtCompactTokens(report.total_input)}</div>
          <div className="usage-metric-sub">{fmtTokens(report.total_input)}</div>
        </button>
        <button type="button" className="usage-metric" onClick={() => setDim("overview")}>
          <div className="usage-metric-label">Received</div>
          <div className="usage-metric-value">{fmtCompactTokens(report.total_output)}</div>
          <div className="usage-metric-sub">{fmtTokens(report.total_output)}</div>
        </button>
        <button type="button" className="usage-metric" onClick={() => setDim("overview")}>
          <div className="usage-metric-label">Cache</div>
          <div className="usage-metric-value">{fmtCompactTokens(report.total_cache_read)}</div>
          <div className="usage-metric-sub">{report.cache_hit_rate.toFixed(1)}% hit</div>
        </button>
        <button type="button" className="usage-metric" onClick={() => setDim("overview")}>
          <div className="usage-metric-label">Est. cost</div>
          <div className="usage-metric-value">
            {report.cost_usd == null ? "—" : fmtCostUsd(report.cost_usd)}
          </div>
          <div className="usage-metric-sub">Writes {fmtCompactTokens(report.total_cache_write)}</div>
        </button>
        <button
          type="button"
          className={`usage-metric${dim === "duckies" ? " is-active" : ""}`}
          onClick={() => setDim("duckies")}
        >
          <div className="usage-metric-label">Calls</div>
          <div className="usage-metric-value">{fmtTokens(report.call_count)}</div>
          <div className="usage-metric-sub">{duckies.length} duckies</div>
        </button>
      </div>

      <div className="usage-dim-tabs" role="tablist" aria-label="Usage breakdown">
        {DIMS.map((d) => (
          <button
            key={d.id}
            type="button"
            role="tab"
            aria-selected={dim === d.id}
            className={`usage-dim-tab${dim === d.id ? " is-active" : ""}`}
            onClick={() => setDim(d.id)}
          >
            {d.label}
          </button>
        ))}
      </div>

      {dim === "overview" ? (
        <>
          <div className="usage-charts-row">
            <div className="usage-chart-block">
              <h4 className="usage-section-title">Sent vs received</h4>
              <div className="usage-chart-body">
                <DonutChart
                  slices={ioSlices}
                  centerLabel={fmtCompactTokens(report.total_tokens)}
                  centerSub="total"
                />
                <ul className="usage-legend">
                  {ioSlices.map((s) => (
                    <li key={s.id}>
                      <span className="usage-swatch" style={{ background: s.color }} />
                      <span className="usage-legend-name">{s.label}</span>
                      <span className="usage-legend-val">{fmtCompactTokens(s.value)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>

            <div className="usage-chart-block">
              <h4 className="usage-section-title">By model</h4>
              <div className="usage-chart-body">
                <DonutChart
                  slices={
                    modelSlices.length ? modelSlices : [{ label: "none", value: 0, color: "var(--border)" }]
                  }
                  centerLabel={modelSlices.length ? String(modelSlices.length) : "0"}
                  centerSub="models"
                  onSliceClick={(s) => {
                    setDim("models");
                    setOpenId(s.id || s.label);
                  }}
                />
                <ul className="usage-legend">
                  {modelSlices.length === 0 ? (
                    <li className="usage-legend-empty">No model traffic yet</li>
                  ) : null}
                  {modelSlices.map((s) => (
                    <li key={s.id}>
                      <button
                        type="button"
                        className="usage-legend-btn"
                        onClick={() => {
                          setDim("models");
                          setOpenId(s.id);
                        }}
                      >
                        <span className="usage-swatch" style={{ background: s.color }} />
                        <span className="usage-legend-name" title={s.label}>
                          {s.label}
                        </span>
                        <span className="usage-legend-val">{fmtCompactTokens(s.value)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            </div>

            <div className="usage-chart-block">
              <h4 className="usage-section-title">By agent</h4>
              <div className="usage-chart-body">
                <DonutChart
                  slices={
                    agentSlices.length ? agentSlices : [{ label: "none", value: 0, color: "var(--border)" }]
                  }
                  centerLabel={agentSlices.length ? String(agentSlices.length) : "0"}
                  centerSub="agents"
                  onSliceClick={(s) => {
                    setDim("agents");
                    setOpenId(s.id || s.label);
                  }}
                />
                <ul className="usage-legend">
                  {agentSlices.length === 0 ? (
                    <li className="usage-legend-empty">No agent traffic yet</li>
                  ) : null}
                  {agentSlices.map((s) => (
                    <li key={s.id}>
                      <button
                        type="button"
                        className="usage-legend-btn"
                        onClick={() => {
                          setDim("agents");
                          setOpenId(s.id);
                        }}
                      >
                        <span className="usage-swatch" style={{ background: s.color }} />
                        <span className="usage-legend-name" title={s.label}>
                          {s.label}
                        </span>
                        <span className="usage-legend-val">{fmtCompactTokens(s.value)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>

          <section className="usage-section">
            <div className="usage-detail-head">
              <h4 className="usage-section-title">Tokens per day</h4>
              <div className="usage-day-legend">
                <span>
                  <span className="usage-swatch" style={{ background: "var(--amber)" }} /> Sent
                </span>
                <span>
                  <span className="usage-swatch" style={{ background: "var(--green)" }} /> Received
                </span>
              </div>
            </div>
            {report.by_day.length ? (
              <DayBars
                days={report.by_day}
                onDayClick={(d) => {
                  setDim("days");
                  setOpenId(d.date);
                }}
              />
            ) : (
              <div className="usage-empty">No usage logged in this window yet.</div>
            )}
          </section>

          <section className="usage-detail">
            <div className="usage-detail-head">
              <h4 className="usage-detail-title">Top Duckies</h4>
              <button type="button" className="usage-dim-tab" onClick={() => setDim("duckies")}>
                See all
              </button>
            </div>
            {duckies.length === 0 ? (
              <div className="usage-empty">No ducky traffic yet.</div>
            ) : (
              <ul className="usage-detail-list">
                {duckyRows(duckies)
                  .slice(0, 5)
                  .map((row) => (
                    <li key={row.id}>
                      <button
                        type="button"
                        className="usage-detail-row"
                        onClick={() => {
                          setDim("duckies");
                          setOpenId(row.id);
                        }}
                      >
                        <span className="usage-detail-row-main">
                          <span className="usage-detail-row-name">{row.name}</span>
                          <span className="usage-detail-row-meta">{row.meta}</span>
                        </span>
                        <span className="usage-detail-row-tokens">{fmtCompactTokens(row.tokens)}</span>
                      </button>
                    </li>
                  ))}
              </ul>
            )}
          </section>
        </>
      ) : null}

      {showList ? (
        <section className="usage-detail">
          <div className="usage-detail-head">
            <h4 className="usage-detail-title">{detailTitle}</h4>
            <span className="usage-detail-hint">Press a row for details</span>
          </div>
          {dim === "days" && report.by_day.length ? (
            <DayBars
              days={report.by_day}
              selectedDate={openId}
              onDayClick={(d) => setOpenId((prev) => (prev === d.date ? null : d.date))}
            />
          ) : null}
          {detailRows.length === 0 ? (
            <div className="usage-empty">Nothing in this breakdown yet.</div>
          ) : (
            <ul className="usage-detail-list">
              {detailRows.map((row) => {
                const open = openId === row.id;
                return (
                  <li key={row.id}>
                    <button
                      type="button"
                      className={`usage-detail-row${open ? " is-open" : ""}`}
                      onClick={() => setOpenId((prev) => (prev === row.id ? null : row.id))}
                      aria-expanded={open}
                    >
                      <span className="usage-detail-row-main">
                        <span className="usage-detail-row-name">{row.name}</span>
                        <span className="usage-detail-row-meta">{row.meta}</span>
                      </span>
                      <span className="usage-detail-row-tokens">{fmtCompactTokens(row.tokens)}</span>
                      {open ? (
                        <span className="usage-detail-expand">
                          <span>
                            Sent
                            <strong>{fmtCompactTokens(row.input)}</strong>
                          </span>
                          <span>
                            Received
                            <strong>{fmtCompactTokens(row.output)}</strong>
                          </span>
                          <span>
                            Calls
                            <strong>{fmtTokens(row.calls)}</strong>
                          </span>
                          <span>
                            Cost
                            <strong>{row.cost > 0 ? fmtCostUsd(row.cost) : "—"}</strong>
                          </span>
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      ) : null}
    </div>
  );
}
