import { useMemo } from "react";

import { HistoryDateRangeHeaderButton, useHistoryDateRange } from "../../components/history/HistoryDateRangePicker";
import { VerseHistoryBody } from "./VerseAuxPanel";

export function useVerseHistoryDockPanel(
  versePath: string | undefined,
  enabled: boolean,
  historyRefreshKey: number,
) {
  const { range, setRange, reset } = useHistoryDateRange();

  const actions = useMemo(
    () => <HistoryDateRangeHeaderButton range={range} onChange={setRange} onReset={reset} />,
    [range, setRange, reset],
  );

  const children = (
    <div className="verse-outline-body">
      <VerseHistoryBody
        filePath={versePath}
        enabled={enabled}
        historyRefreshKey={historyRefreshKey}
        dateRange={range}
      />
    </div>
  );

  return { actions, children };
}
