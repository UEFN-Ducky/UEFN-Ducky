import { dockDropInsertIndex, type DockDropTarget } from "../utils/dockPanelDrag";
import type { DockPanelId, DockSide, WorkspaceDockSnapshot } from "./workspaceDockStorage";
import { panelsOnSide } from "./workspaceDockStorage";

const SIDEBAR_FAMILY: DockPanelId[] = ["chats", "files"];
const VERSE_FAMILY: DockPanelId[] = ["outline", "history", "tester"];

/** Resolve insert index for a tab drop, respecting top/center/bottom zones. */
export function insertIndexForTabDrop(
  dropTarget: DockDropTarget,
  targetSide: DockSide,
  snapshot: WorkspaceDockSnapshot,
  sectionPanelIds?: DockPanelId[],
): number | undefined {
  if (!dropTarget || dropTarget.side !== targetSide) return undefined;

  const onSide = panelsOnSide(snapshot, targetSide);
  const sectionIds = sectionPanelIds ?? onSide;
  const sectionCount = sectionIds.length;

  if (dropTarget.insert === "top") return 0;

  if (dropTarget.insert === "bottom") {
    if (!sectionPanelIds) {
      const sidebarOnSide = onSide.filter((id) => SIDEBAR_FAMILY.includes(id));
      const verseOnSide = onSide.filter((id) => VERSE_FAMILY.includes(id));
      if (sidebarOnSide.length > 0 && verseOnSide.length > 0) {
        return sidebarOnSide.length;
      }
    }
    return sectionCount;
  }

  return dockDropInsertIndex(dropTarget, sectionPanelIds ? sectionCount : onSide.length);
}

export function countPanelsOnRail(side: DockSide): number {
  const rail = document.querySelector<HTMLElement>(`.dock-rail--${side}`);
  if (!rail) return 0;
  const tabBodies = rail.querySelectorAll<HTMLElement>("[data-dock-panel-id]").length;
  if (tabBodies > 0) return tabBodies;
  return rail.querySelectorAll<HTMLElement>(".sidebar-stacked-panel").length;
}
