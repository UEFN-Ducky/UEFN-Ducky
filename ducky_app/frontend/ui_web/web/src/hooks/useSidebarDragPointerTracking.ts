import { useEffect, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import {
  classifySidebarDragOut,
  setSidebarEditorDropPreview,
  type SidebarDragPoint,
} from "../utils/sidebarDragOut";

/** Tracks pointer position during a sidebar drag so row drop hints (which need the
 * current Y) and drag-out detection (which needs the current X/Y and screen coords)
 * stay up to date between dnd-kit's onDragOver ticks. Shared by SidebarFileTree and
 * SidebarFolderTree, whose drag-tracking is otherwise identical.
 *
 * While the pointer is over the editor (left/right of the dock rails), clears row
 * reorder hints and publishes the VS Code-style split-zone preview.
 *
 * `refreshDropHint` recomputes before/inside/after for the current over row as the
 * pointer moves within that row (onDragOver alone won't re-fire). */
export function useSidebarDragPointerTracking<DropHint>(
  activeDragId: string | null,
  pointerYRef: MutableRefObject<number>,
  dragPointRef: MutableRefObject<SidebarDragPoint>,
  setDropHint: Dispatch<SetStateAction<DropHint | null>>,
  refreshDropHint?: () => void,
) {
  useEffect(() => {
    if (!activeDragId) {
      setSidebarEditorDropPreview(null);
      return;
    }
    const onMove = (e: PointerEvent) => {
      pointerYRef.current = e.clientY;
      dragPointRef.current = {
        clientX: e.clientX,
        clientY: e.clientY,
        screenX: e.screenX,
        screenY: e.screenY,
      };
      const zone = classifySidebarDragOut(dragPointRef.current);
      if (zone?.kind === "editor") {
        // Row drop hints are meaningless once the drag leaves the docks.
        setDropHint(null);
        setSidebarEditorDropPreview({ groupId: zone.groupId, zone: zone.zone });
      } else {
        setSidebarEditorDropPreview(null);
        refreshDropHint?.();
      }
    };
    window.addEventListener("pointermove", onMove);
    return () => {
      window.removeEventListener("pointermove", onMove);
      setSidebarEditorDropPreview(null);
    };
  }, [activeDragId, pointerYRef, dragPointRef, setDropHint, refreshDropHint]);
}
