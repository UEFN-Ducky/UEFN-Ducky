import type { ReactNode, Ref } from "react";
import { TruncatedText } from "../TruncatedText";

interface SidebarTreeRowProps {
  leading: ReactNode;
  label: string;
  title?: string;
  labelClassName?: string;
  /** Optional second line under the label (chat meta, etc.). */
  meta?: ReactNode;
  isEditing?: boolean;
  renameInput?: ReactNode;
  actions?: ReactNode;
  contextMenu?: ReactNode;
  isActive?: boolean;
  isParentSelected?: boolean;
  isFocused?: boolean;
  isNew?: boolean;
  rowRef?: Ref<HTMLDivElement>;
  dataAttr: "data-sidebar-id" | "data-file-id";
  dataId: string;
  /** Id in the shared tree drag-and-drop (tree-dnd). */
  treeId?: string;
  /** Can be picked up and dragged. */
  draggable?: boolean;
  onClick?: (e: React.MouseEvent) => void;
  onDoubleClick?: (e: React.MouseEvent) => void;
  onContextMenu?: (e: React.MouseEvent) => void;
}

export function SidebarTreeRow({
  leading,
  label,
  title,
  labelClassName,
  meta,
  isEditing = false,
  renameInput,
  actions,
  contextMenu,
  isActive = false,
  isParentSelected = false,
  isFocused = false,
  isNew = false,
  rowRef,
  dataAttr,
  dataId,
  treeId,
  draggable = false,
  onClick,
  onDoubleClick,
  onContextMenu,
}: SidebarTreeRowProps) {
  return (
    <div
      ref={rowRef}
      {...{ [dataAttr]: dataId }}
      data-tree-row={treeId ?? dataId}
      role="button"
      tabIndex={0}
      aria-label={label}
      draggable={draggable && !isEditing}
      className={[
        "sidebar-tree-row",
        "group",
        meta ? "sidebar-tree-row--with-meta" : "",
        isActive ? "is-active" : "",
        isParentSelected ? "is-parent-selected" : "",
        isFocused ? "is-focused" : "",
        isNew ? "sidebar-item-enter" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      onContextMenu={onContextMenu}
    >
      {leading}
      {isEditing ? (
        renameInput
      ) : (
        <div className="sidebar-tree-row-text">
          <TruncatedText
            className={["sidebar-tree-row-label", labelClassName].filter(Boolean).join(" ")}
            title={title ?? label}
          >
            {label}
          </TruncatedText>
          {meta ? <div className="sidebar-tree-row-meta">{meta}</div> : null}
        </div>
      )}
      {!isEditing ? actions : null}
      {contextMenu}
    </div>
  );
}
