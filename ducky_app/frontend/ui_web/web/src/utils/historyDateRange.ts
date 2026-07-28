import type { FileHistoryEntry } from "../types/panel";

export type HistoryDateRange = {
  fromMs: number | null;
  toMs: number | null;
};

export const EMPTY_HISTORY_DATE_RANGE: HistoryDateRange = { fromMs: null, toMs: null };

export function historyDateRangeIsActive(range: HistoryDateRange): boolean {
  return range.fromMs != null || range.toMs != null;
}

function startOfDay(date: Date): number {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function endOfDay(date: Date): number {
  const d = new Date(date);
  d.setHours(23, 59, 59, 999);
  return d.getTime();
}

export type HistoryDatePreset = {
  id: string;
  label: string;
  range: () => HistoryDateRange;
};

export const HISTORY_DATE_PRESETS: HistoryDatePreset[] = [
  {
    id: "today",
    label: "Today",
    range: () => ({ fromMs: startOfDay(new Date()), toMs: Date.now() }),
  },
  {
    id: "yesterday",
    label: "Yesterday",
    range: () => {
      const d = new Date();
      d.setDate(d.getDate() - 1);
      return { fromMs: startOfDay(d), toMs: endOfDay(d) };
    },
  },
  {
    id: "7d",
    label: "Last 7 days",
    range: () => {
      const d = new Date();
      d.setDate(d.getDate() - 7);
      return { fromMs: startOfDay(d), toMs: Date.now() };
    },
  },
  {
    id: "30d",
    label: "Last 30 days",
    range: () => {
      const d = new Date();
      d.setDate(d.getDate() - 30);
      return { fromMs: startOfDay(d), toMs: Date.now() };
    },
  },
];

export function filterHistoryByDateRange(
  entries: FileHistoryEntry[],
  range: HistoryDateRange,
): FileHistoryEntry[] {
  if (!historyDateRangeIsActive(range)) return entries;
  return entries.filter((entry) => {
    const ts = entry.saved_at * 1000;
    if (range.fromMs != null && ts < range.fromMs) return false;
    if (range.toMs != null && ts > range.toMs) return false;
    return true;
  });
}

export function toDateInputValue(ms: number | null): string {
  if (ms == null) return "";
  const d = new Date(ms);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function toTimeInputValue(ms: number | null): string {
  if (ms == null) return "";
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function parseDateTime(date: string, time: string): number | null {
  if (!date) return null;
  const parsed = new Date(`${date}T${time || "00:00"}`);
  return Number.isNaN(parsed.getTime()) ? null : parsed.getTime();
}

export function formatHistoryDateRangeLabel(range: HistoryDateRange): string {
  if (!historyDateRangeIsActive(range)) return "Filter by date";

  const fmt = (ms: number) =>
    new Date(ms).toLocaleDateString(undefined, { month: "short", day: "numeric" });

  if (range.fromMs != null && range.toMs != null) {
    return `${fmt(range.fromMs)} – ${fmt(range.toMs)}`;
  }
  if (range.fromMs != null) return `From ${fmt(range.fromMs)}`;
  if (range.toMs != null) return `Until ${fmt(range.toMs)}`;
  return "Filter by date";
}
