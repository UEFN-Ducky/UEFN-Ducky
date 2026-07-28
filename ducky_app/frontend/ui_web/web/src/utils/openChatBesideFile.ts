import type { ChatTab, EditorLayoutState, EditorTab } from "../types/panel";
import { chatTabId } from "../types/panel";
import { activateTab, findGroupForTab, focusGroup, handleGroupDrop } from "./editorLayoutOps";

interface OpenChatBesideFileOptions {
  chat: ChatTab;
  layout: EditorLayoutState;
  fileTabId?: string;
  openTab: (tab: EditorTab) => void;
  setLayout: (updater: (prev: EditorLayoutState) => EditorLayoutState) => void;
  activateTabInGroup: (groupId: string, tabId: string) => void;
  setFocusedGroup: (groupId: string) => void;
}

export function openChatBesideFile({
  chat,
  layout,
  fileTabId,
  openTab,
  setLayout,
  activateTabInGroup,
  setFocusedGroup,
}: OpenChatBesideFileOptions): void {
  const id = chatTabId(chat.id);
  const existingGroup = findGroupForTab(layout, id);

  if (!existingGroup) {
    openTab({
      id,
      kind: "chat",
      name: chat.name,
      chatId: chat.id,
      duckyStyle: chat.duckyStyle,
    });
    setLayout((prev) => {
      const chatGroup = findGroupForTab(prev, id);
      if (!chatGroup) return prev;
      if (fileTabId) {
        const fileGroup = findGroupForTab(prev, fileTabId);
        if (fileGroup === chatGroup) {
          return handleGroupDrop(prev, chatGroup, id, chatGroup, "right");
        }
      }
      return focusGroup(activateTab(prev, chatGroup, id), chatGroup);
    });
    return;
  }

  activateTabInGroup(existingGroup, id);
  setFocusedGroup(existingGroup);
}
