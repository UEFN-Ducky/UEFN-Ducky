import type { MouseEvent, PointerEvent, ReactNode } from "react";

export function SidebarSectionHeader({
  title,
  icon,
  busy = false,
  busyTitle = "Working…",
  actions,
  onContextMenu,
  headerClassName = "",
  onHeaderPointerDown,
  draggable = false,
}: {
  title: string;
  icon?: ReactNode;
  /** Visible on the header even when the panel body is collapsed. */
  busy?: boolean;
  busyTitle?: string;
  actions?: ReactNode;
  onContextMenu?: (e: MouseEvent) => void;
  headerClassName?: string;
  onHeaderPointerDown?: (e: PointerEvent<HTMLDivElement>) => void;
  draggable?: boolean;
}) {
  return (
    <div
      className={`sidebar-section-header${onContextMenu ? " sidebar-section-header--context" : ""}${draggable ? " sidebar-section-header--draggable" : ""}${headerClassName ? ` ${headerClassName}` : ""}${busy ? " is-busy" : ""}`}
      onContextMenu={
        onContextMenu
          ? (e) => {
              e.preventDefault();
              onContextMenu(e);
            }
          : undefined
      }
      onPointerDown={onHeaderPointerDown}
      aria-busy={busy || undefined}
    >
      {icon ? <span className="sidebar-section-header-icon" aria-hidden="true">{icon}</span> : null}
      <span className="sidebar-section-header-title">{title}</span>
      {busy ? (
        <span
          className="sidebar-agent-spinner sidebar-section-header-busy"
          title={busyTitle}
          aria-label={busyTitle}
        />
      ) : null}
      {actions ? <div className="sidebar-section-header-actions">{actions}</div> : null}
    </div>
  );
}
