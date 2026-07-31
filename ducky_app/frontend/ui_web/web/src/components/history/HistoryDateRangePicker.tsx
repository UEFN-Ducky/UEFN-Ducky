import { useCallback, useRef, useState } from "react";

import { DropdownPanel } from "../DropdownPanel";
import { Icons } from "../../icons/Icons";
import {
  EMPTY_HISTORY_DATE_RANGE,
  HISTORY_DATE_PRESETS,
  formatHistoryDateRangeLabel,
  historyDateRangeIsActive,
  parseDateTime,
  toDateInputValue,
  toTimeInputValue,
  type HistoryDateRange,
} from "../../utils/historyDateRange";

function DateTimeField({
  label,
  date,
  time,
  onDateChange,
  onTimeChange,
}: {
  label: string;
  date: string;
  time: string;
  onDateChange: (value: string) => void;
  onTimeChange: (value: string) => void;
}) {
  return (
    <div className="history-date-field">
      <span className="history-date-field-label">{label}</span>
      <div className="history-date-field-inputs">
        <input
          type="date"
          className="history-date-input"
          value={date}
          aria-label={`${label} date`}
          onChange={(e) => onDateChange(e.target.value)}
        />
        <input
          type="time"
          className="history-date-input history-date-input--time"
          value={time}
          aria-label={`${label} time`}
          onChange={(e) => onTimeChange(e.target.value)}
        />
      </div>
    </div>
  );
}

export function HistoryDateRangePickerPanel({
  range,
  onChange,
  onReset,
}: {
  range: HistoryDateRange;
  onChange: (range: HistoryDateRange) => void;
  onReset: () => void;
}) {
  const fromDate = toDateInputValue(range.fromMs);
  const fromTime = toTimeInputValue(range.fromMs);
  const toDate = toDateInputValue(range.toMs);
  const toTime = toTimeInputValue(range.toMs);

  const updateFrom = useCallback(
    (date: string, time: string) => {
      onChange({ ...range, fromMs: parseDateTime(date, time) });
    },
    [onChange, range],
  );

  const updateTo = useCallback(
    (date: string, time: string) => {
      if (!date) {
        onChange({ ...range, toMs: null });
        return;
      }
      let toMs: number | null;
      if (!time) {
        const end = new Date(`${date}T12:00:00`);
        end.setHours(23, 59, 59, 999);
        toMs = end.getTime();
      } else {
        const start = parseDateTime(date, time);
        toMs = start == null ? null : start + 59_999;
      }
      onChange({ ...range, toMs });
    },
    [onChange, range],
  );

  const applyPreset = useCallback(
    (presetId: string) => {
      const preset = HISTORY_DATE_PRESETS.find((p) => p.id === presetId);
      if (preset) onChange(preset.range());
    },
    [onChange],
  );

  return (
    <div className="history-date-picker">
      <div className="history-date-picker-section">
        <span className="history-date-picker-heading">Quick range</span>
        <div className="history-date-presets">
          {HISTORY_DATE_PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              className="history-date-preset-btn"
              onClick={() => applyPreset(preset.id)}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>

      <DateTimeField
        label="From"
        date={fromDate}
        time={fromTime}
        onDateChange={(date) => updateFrom(date, fromTime)}
        onTimeChange={(time) => updateFrom(fromDate, time)}
      />
      <DateTimeField
        label="To"
        date={toDate}
        time={toTime}
        onDateChange={(date) => updateTo(date, toTime)}
        onTimeChange={(time) => updateTo(toDate, time)}
      />

      <div className="history-date-picker-footer">
        <button
          type="button"
          className="history-date-reset-btn"
          disabled={!historyDateRangeIsActive(range)}
          onClick={onReset}
        >
          Reset
        </button>
      </div>
    </div>
  );
}

export function HistoryDateRangeHeaderButton({
  range,
  onChange,
  onReset,
  disabled = false,
}: {
  range: HistoryDateRange;
  onChange: (range: HistoryDateRange) => void;
  onReset: () => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const isActive = historyDateRangeIsActive(range);
  const title = formatHistoryDateRangeLabel(range);

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className={`verse-outline-header-btn${isActive ? " is-active" : ""}`}
        title={title}
        aria-label={title}
        aria-pressed={isActive}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        <Icons.Calendar />
      </button>
      <DropdownPanel
        anchorRef={btnRef}
        open={open && !disabled}
        onClose={() => setOpen(false)}
        minWidth={240}
        width={240}
        placement="bottom"
      >
        <HistoryDateRangePickerPanel
          range={range}
          onChange={onChange}
          onReset={() => {
            onReset();
            setOpen(false);
          }}
        />
      </DropdownPanel>
    </>
  );
}

export function useHistoryDateRange() {
  const [range, setRange] = useState<HistoryDateRange>(EMPTY_HISTORY_DATE_RANGE);
  const reset = useCallback(() => setRange(EMPTY_HISTORY_DATE_RANGE), []);
  return { range, setRange, reset, isActive: historyDateRangeIsActive(range) };
}
