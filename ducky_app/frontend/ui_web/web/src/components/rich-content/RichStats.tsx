import { Icons } from "../../icons/Icons";
import type { RichProgramKey } from "../../types/richContent";

interface RichStatsProps {
  changes?: number;
  blocked?: number;
  programs?: Partial<Record<RichProgramKey, number>>;
}

const PROGRAMS: Array<{ key: RichProgramKey; label: string }> = [
  { key: "uefn", label: "UEFN" },
  { key: "blender", label: "Blender" },
  { key: "verse", label: "Verse" },
  { key: "file", label: "File" },
];

function programBarRects(segs: Array<{ key: RichProgramKey; count: number }>, total: number) {
  const gap = segs.length > 1 ? 0.4 : 0;
  const usable = 100 - gap * Math.max(0, segs.length - 1);
  let x = 0;
  return segs.map((s) => {
    const w = total > 0 ? (s.count / total) * usable : 0;
    const rect = { key: s.key, x, w };
    x += w + gap;
    return rect;
  });
}

export function RichStats({ changes, blocked, programs }: RichStatsProps) {
  const segs = PROGRAMS.map(({ key, label }) => ({
    key,
    label,
    count: Number(programs?.[key] ?? 0) || 0,
  })).filter((s) => s.count > 0);
  const total = segs.reduce((n, s) => n + s.count, 0);
  const showNums = changes != null || blocked != null;
  const showBar = segs.length > 0;
  if (!showNums && !showBar) return null;

  return (
    <div className="rich-stats-block">
      <h2 className="rich-stats-title">Run Summary</h2>
      <div className="rich-stats">
        {showNums ? (
          <div className="rich-stats-nums">
            {changes != null ? (
              <div>
                <div className="rich-stats-metric-label">Editor Changes</div>
                <div className="rich-stats-metric-row">
                  <div className="rich-stats-num">{changes}</div>
                  <span className="rich-stats-hint rich-stats-hint--ok">
                    <Icons.Check /> Applied
                  </span>
                </div>
              </div>
            ) : null}
            {blocked != null ? (
              <div>
                <div className="rich-stats-metric-label">Blocked Ops</div>
                <div className="rich-stats-metric-row">
                  <div className="rich-stats-num rich-stats-num--blocked">{blocked}</div>
                  <span className="rich-stats-hint rich-stats-hint--blocked">
                    <Icons.AlertTriangle /> Retries
                  </span>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
        {showBar ? (
          <div className="rich-stats-programs">
            <div className="rich-stats-programs-head">
              <span className="rich-stats-metric-label">Programs Tracker</span>
              <span className="rich-stats-programs-total">{total} Total</span>
            </div>
            <svg className="rich-stats-bar" viewBox="0 0 100 4" preserveAspectRatio="none" aria-hidden>
              {programBarRects(segs, total).map((r) => (
                <rect
                  key={r.key}
                  x={r.x}
                  y="0"
                  width={r.w}
                  height="4"
                  className={`rich-stats-bar-seg rich-stats-bar-seg--${r.key}`}
                />
              ))}
            </svg>
            <div className="rich-stats-legend">
              {segs.map((s) => (
                <span key={s.key} className={`rich-stats-legend-item rich-tone--${{ uefn: "blue", blender: "green", verse: "purple", file: "amber" }[s.key]}`}>
                  <span className={`rich-stats-legend-dot rich-stats-legend-dot--${s.key}`} />
                  {s.label}
                  <strong className="rich-stats-legend-count">{s.count}</strong>
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
