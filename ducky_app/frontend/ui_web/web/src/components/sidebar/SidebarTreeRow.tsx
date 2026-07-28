import { CSS } from "@dnd-kit/utilities";
import type { DraggableAttributes } from "@dnd-kit/core";
import type { SyntheticListenerMap } from "@dnd-kit/core/dist/hooks/utilities";
import type { ReactNode } from "react";
import { ScopedCss } from "../../utils/scopedCss";
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
  isDragging?: boolean;
  isNew?: boolean;
  dropClass?: string;
  rowScopeClass: string;
  dndTransform: ReturnType<typeof CSS.Transform.toString> | null;
  dndTransition: string | undefined;
  mergeRowRef: (node: HTMLDivElement | null) => void;
  dataAttr: "data-sidebar-id" | "data-file-id";
  dataId: string;
  attributes?: DraggableAttributes;
  listeners?: SyntheticListenerMap;
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
  isDragging = false,
  isNew = false,
  dropClass = "",
  rowScopeClass,
  dndTransform,
  dndTransition,
  mergeRowRef,
  dataAttr,
  dataId,
  attributes,
  listeners,
  onClick,
  onDoubleClick,
  onContextMenu,
}: SidebarTreeRowProps) {
  return (
    <>
      <ScopedCss
        selector={`.${rowScopeClass}`}
        rules={{
          "--dnd-transform": dndTransform ?? "none",
          "--dnd-transition": dndTransition ?? "",
        }}
      />
      <div
        ref={mergeRowRef}
        {...{ [dataAttr]: dataId }}
        className={[
          "sidebar-tree-row",
          "group",
          "dnd-sortable",
          rowScopeClass,
          meta ? "sidebar-tree-row--with-meta" : "",
          isActive ? "is-active" : "",
          isParentSelected ? "is-parent-selected" : "",
          isFocused ? "is-focused" : "",
          isDragging ? "is-dragging" : "",
          isNew ? "sidebar-item-enter" : "",
          dropClass,
        ]
          .filter(Boolean)
          .join(" ")}
        {...(attributes ?? {})}
        {...(listeners ?? {})}
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
    </>
  );
}
