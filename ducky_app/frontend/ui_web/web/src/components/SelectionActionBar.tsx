import { Icons } from "../icons/Icons";

export type SelectionAction = { id: string; label: string; title?: string; danger?: boolean; onClick: () => void };

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
    <div className="selection-action-bar" role="toolbar" aria-label="Selection actions">
      <span className="selection-action-bar-count">{`${count} selected`}</span>
      {actions.map((action) => (
        <button
          key={action.id}
          type="button"
          className={`selection-action-bar-btn${action.danger ? " is-danger" : ""}`}
          title={action.title ?? action.label}
          onClick={action.onClick}
        >
          {action.label}
        </button>
      ))}
      <button
        type="button"
        className="icon-btn selection-action-bar-clear"
        aria-label="Clear selection"
        title="Clear selection"
        onClick={onClear}
      >
        <Icons.Close />
      </button>
    </div>
  );
}
