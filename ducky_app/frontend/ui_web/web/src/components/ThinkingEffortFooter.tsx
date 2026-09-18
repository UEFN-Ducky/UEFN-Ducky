import {
  effortInMenu,
  footerThinkingMenu,
  formatEffortReadout,
  menuLevel,
  thinkingSliderEnabled,
  type ThinkingMenu,
} from "./thinkingMenu";

interface ThinkingEffortFooterProps {
  menu: ThinkingMenu | null | undefined;
  modelName: string;
  effort: string;
  onChange: (id: string) => void;
}

/** Pinned Faster/Smarter slider. Always visible; disabled when the gateway sent no menu. */
export function ThinkingEffortFooter({ menu, modelName, effort, onChange }: ThinkingEffortFooterProps) {
  const resolved = footerThinkingMenu(menu);
  const levels = resolved.levels;
  const enabled = thinkingSliderEnabled(menu);
  const id = effortInMenu(resolved, enabled ? effort : "off");
  const index = Math.max(
    0,
    levels.findIndex((l) => (l.id || "").toLowerCase() === id),
  );
  const level = menuLevel(resolved, id);
  const lo = resolved.lo || "Faster";
  const hi = resolved.hi || "Smarter";
  const last = Math.max(0, levels.length - 1);
  const pct = last === 0 ? 0 : index / last;

  return (
    <div
      className={`model-selector-effort${enabled ? "" : " is-disabled"}`}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="model-selector-effort-readout">{formatEffortReadout(modelName, level)}</div>
      {level?.hint ? <div className="model-selector-effort-hint">{level.hint}</div> : null}
      <div className="model-selector-effort-labels">
        <span>{lo}</span>
        <span>{hi}</span>
      </div>
      <div className="model-selector-effort-slider">
        <div className="model-selector-effort-rail" aria-hidden="true">
          <span className="model-selector-effort-dots" />
          <span className="model-selector-effort-ticks">
            {levels.map((row, i) => (
              <i
                key={row.id || i}
                className={i <= index ? "is-on" : ""}
                style={{ left: `${last === 0 ? 0 : (i / last) * 100}%` }}
              />
            ))}
          </span>
          <span className="model-selector-effort-thumb" style={{ left: `${pct * 100}%` }} />
        </div>
        <input
          type="range"
          className="model-selector-effort-range"
          min={0}
          max={last}
          step={1}
          value={index}
          disabled={!enabled}
          aria-label="Thinking effort"
          onChange={(e) => {
            if (!enabled) return;
            const next = levels[Number(e.target.value)];
            if (next?.id) onChange(next.id);
          }}
        />
      </div>
    </div>
  );
}
