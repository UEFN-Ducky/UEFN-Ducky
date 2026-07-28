import { useCallback, useEffect, useState } from "react";
import { getApi } from "../../hooks/usePanelApi";
import type { DuckyUsageReport } from "../../types/panel";
import { fmtCompactTokens, fmtCostUsd, fmtTokens } from "../../utils/contextFormat";

type Props = {
  duckyName: string;
  profileId?: string;
  days?: number;
};

function emptyReport(duckyName: string, profileId: string, days: number): DuckyUsageReport {
  return {
    ducky_name: duckyName,
    profile_id: profileId,
    days,
    chat_count: 0,
    call_count: 0,
    total_input: 0,
    total_output: 0,
    total_tokens: 0,
    total_cache_read: 0,
    total_cache_write: 0,
    cost_usd: null,
    chats: [],
  };
}

/** Compact last-N-days stats for one ducky profile (chats + tokens). */
export function DuckyProfileStats({ duckyName, profileId = "", days = 7 }: Props) {
  const [report, setReport] = useState<DuckyUsageReport>(() =>
    emptyReport(duckyName, profileId, days),
  );
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const api = getApi();
    if (!api?.get_ducky_usage || (!duckyName.trim() && !profileId.trim())) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const data = await api.get_ducky_usage(duckyName, profileId, days);
      setReport(data);
    } catch {
      setReport(emptyReport(duckyName, profileId, days));
    } finally {
      setLoading(false);
    }
  }, [duckyName, profileId, days]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const cells = [
    { label: "Chats", value: fmtTokens(report.chat_count), title: `${report.chat_count} chats` },
    { label: "Calls", value: fmtTokens(report.call_count), title: `${report.call_count} API calls` },
    {
      label: "Tokens",
      value: fmtCompactTokens(report.total_tokens),
      title: `${fmtTokens(report.total_input)} sent · ${fmtTokens(report.total_output)} received`,
    },
    {
      label: "Est. cost",
      value: report.cost_usd != null && report.cost_usd > 0 ? fmtCostUsd(report.cost_usd) : "—",
      title: report.cost_usd != null ? fmtCostUsd(report.cost_usd) : "No priced calls",
    },
  ];

  return (
    <section className="ducky-profile-stats" aria-label={`Usage last ${days} days`}>
      <div className="ducky-profile-stats-head">
        <span className="ducky-profile-stats-title">Usage</span>
        <span className="ducky-profile-stats-sub">Last {days} days</span>
      </div>
      <div className={`ducky-profile-stats-grid${loading ? " is-loading" : ""}`}>
        {cells.map((c) => (
          <div key={c.label} className="ducky-profile-stats-cell" title={c.title}>
            <span className="ducky-profile-stats-label">{c.label}</span>
            <span className="ducky-profile-stats-value">{c.value}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
