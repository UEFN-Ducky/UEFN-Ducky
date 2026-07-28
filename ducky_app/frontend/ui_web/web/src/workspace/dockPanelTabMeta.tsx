import type { ReactNode } from "react";
import { Icons } from "../icons/Icons";
import type { DockPanelId } from "./workspaceDockStorage";

export type DockPanelTabMeta = {
  id: DockPanelId;
  title: string;
  headerTitle: string;
  icon: ReactNode;
};

export const DOCK_PANEL_TAB_META: Record<DockPanelId, DockPanelTabMeta> = {
  chats: { id: "chats", title: "Duckies", headerTitle: "Duckies", icon: <Icons.Duck /> },
  files: { id: "files", title: "Verse", headerTitle: "Content", icon: <Icons.Verse /> },
  outline: { id: "outline", title: "Outline", headerTitle: "Outline", icon: <Icons.Outline /> },
  history: { id: "history", title: "History", headerTitle: "History", icon: <Icons.Clock /> },
  tester: { id: "tester", title: "Tester", headerTitle: "Tester", icon: <Icons.Check /> },
  groupchat: { id: "groupchat", title: "Discord Ducky", headerTitle: "Discord Ducky", icon: <Icons.Chat /> },
  discordhub: { id: "discordhub", title: "Discord", headerTitle: "Discord", icon: <Icons.Chat /> },
};

export function dockPanelTabsInOrder(panelIds: DockPanelId[], stackOrder: DockPanelId[]) {
  const ordered = stackOrder.filter((id) => panelIds.includes(id));
  for (const id of panelIds) {
    if (!ordered.includes(id)) ordered.push(id);
  }
  return ordered.map((id) => DOCK_PANEL_TAB_META[id]);
}
