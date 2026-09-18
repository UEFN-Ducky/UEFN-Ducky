import {
  effortInMenu,
  formatEffortReadout,
  menuLevel,
  type ThinkingMenu,
} from "./thinkingMenu";

interface ThinkingEffortFooterProps {
  menu: ThinkingMenu;
  modelName: string;
  effort: string;
  onChange: (id: string) => void;
}

/** Pinned Faster/Smarter slider. Levels come from the gateway, never the host. */
export function ThinkingEffortFooter({ menu, modelName, effort, onChange }: ThinkingEffortFooterProps) {
  const levels = menu.levels;
  const id = effortInMenu(menu, effort);
  const index = Math.max(
    0,
    levels.findIndex((l) => (l.id || "").toLowerCase() === id),
  );
  const level = menuLevel(menu, id);
  const lo = menu.lo || "Faster";
  const hi = menu.hi || "Smarter";
  const last = Math.max(0, levels.length - 1);

  return (
    <div className="model-selector-effort" onClick={(e) => e.stopPropagation()}>
      <div className="model-selector-effort-readout">{formatEffortReadout(modelName, level)}</div>
      {level?.hint ? <div className="model-selector-effort-hint">{level.hint}</div> : null}
      <div className="model-selector-effort-axis">
        <span>{lo}</span>
        <input
          type="range"
          className="model-selector-effort-range"
          min={0}
          max={last}
          step={1}
          value={index}
          aria-label="Thinking effort"
          onChange={(e) => {
            const next = levels[Number(e.target.value)];
            if (next?.id) onChange(next.id);
          }}
        />
        <span>{hi}</span>
      </div>
    </div>
  );
}
