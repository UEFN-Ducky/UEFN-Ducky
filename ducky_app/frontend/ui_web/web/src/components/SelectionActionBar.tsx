import type { CSSProperties } from "react";
import { Icons } from "../icons/Icons";

export type SelectionAction = { id: string; label: string; title?: string; danger?: boolean; onClick: () => void };

const barStyle: CSSProperties = {
  position: "sticky",
  bottom: 0,
  zIndex: 2,
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: 4,
  padding: "4px 6px",
  paddingBottom: "max(4px, env(safe-area-inset-bottom))",
  background: "var(--bg, inherit)",
  borderTop: "1px solid var(--border, rgba(127, 127, 127, 0.3))",
};
// Touch targets: at least 40 px tall.
const buttonStyle: CSSProperties = { minHeight: 40, padding: "0 10px" };

/** "N selected · actions · ✕" at the bottom of a tree, for touch where there is no right-click. */
export function SelectionActionBar({
  count,
  actions,
  onClear,
}: {
  count: number;
  actions: SelectionAction[];
  onClear: () => void;
}) {
  return (
    <div className="selection-action-bar" role="toolbar" aria-label="Selection actions" style={barStyle}>
      <span className="selection-action-bar-count" style={{ marginRight: "auto" }}>{`${count} selected`}</span>
      {actions.map((action) => (
        <button
          key={action.id}
          type="button"
          className={`selection-action-bar-btn${action.danger ? " is-danger" : ""}`}
          title={action.title ?? action.label}
          style={buttonStyle}
          onClick={action.onClick}
        >
          {action.label}
        </button>
      ))}
      <button
        type="button"
        className="selection-action-bar-btn"
        aria-label="Clear selection"
        title="Clear selection"
        style={buttonStyle}
        onClick={onClear}
      >
        <Icons.Close />
      </button>
    </div>
  );
}
