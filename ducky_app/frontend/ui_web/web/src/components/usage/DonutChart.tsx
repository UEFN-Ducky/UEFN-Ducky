/** Simple SVG donut / pie for provider usage reports (no chart lib). */

export type DonutSlice = {
  label: string;
  value: number;
  color: string;
  id?: string;
};

const COLORS = [
  "var(--amber)",
  "var(--accent)",
  "var(--green)",
  "var(--blue)",
  "var(--yellow, var(--amber))",
  "var(--red)",
  "var(--purple, var(--accent))",
  "var(--muted)",
];

function polar(cx: number, cy: number, r: number, angle: number) {
  const rad = ((angle - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function arcPath(cx: number, cy: number, r: number, start: number, end: number) {
  const s = polar(cx, cy, r, end);
  const e = polar(cx, cy, r, start);
  const large = end - start <= 180 ? 0 : 1;
  return `M ${s.x} ${s.y} A ${r} ${r} 0 ${large} 0 ${e.x} ${e.y}`;
}

export function DonutChart({
  slices,
  size = 120,
  thickness = 18,
  centerLabel,
  centerSub,
  onSliceClick,
}: {
  slices: DonutSlice[];
  size?: number;
  thickness?: number;
  centerLabel?: string;
  centerSub?: string;
  onSliceClick?: (slice: DonutSlice) => void;
}) {
  const total = slices.reduce((s, x) => s + Math.max(0, x.value), 0);
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 4;
  const innerR = Math.max(8, r - thickness);
  const clickable = !!onSliceClick;

  let angle = 0;
  const paths: { d: string; color: string; key: string; slice?: DonutSlice }[] = [];
  if (total <= 0) {
    paths.push({
      key: "empty",
      color: "var(--border)",
      d: arcPath(cx, cy, (r + innerR) / 2, 0.01, 359.99),
    });
  } else {
    slices.forEach((slice, i) => {
      const v = Math.max(0, slice.value);
      if (v <= 0) return;
      const sweep = (v / total) * 360;
      const start = angle;
      const end = angle + Math.max(sweep, 0.5);
      const midR = (r + innerR) / 2;
      paths.push({
        key: `${slice.label}-${i}`,
        color: slice.color || COLORS[i % COLORS.length],
        d: arcPath(cx, cy, midR, start, Math.min(end, start + 359.99)),
        slice,
      });
      angle = end;
    });
  }

  return (
    <div className="usage-donut" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
        {paths.map((p) => (
          <path
            key={p.key}
            d={p.d}
            fill="none"
            stroke={p.color}
            strokeWidth={thickness}
            strokeLinecap="butt"
            className={clickable && p.slice ? "usage-donut-slice is-clickable" : undefined}
            onClick={
              clickable && p.slice
                ? (e) => {
                    e.stopPropagation();
                    onSliceClick?.(p.slice!);
                  }
                : undefined
            }
          />
        ))}
      </svg>
      {(centerLabel || centerSub) && (
        <div className="usage-donut-center">
          {centerLabel ? <div className="usage-donut-center-label">{centerLabel}</div> : null}
          {centerSub ? <div className="usage-donut-center-sub">{centerSub}</div> : null}
        </div>
      )}
    </div>
  );
}

export function defaultSliceColors(n: number): string[] {
  return Array.from({ length: n }, (_, i) => COLORS[i % COLORS.length]);
}
