import { useCallback, useMemo } from "react";

import type { ProblemsDuckyHandlers } from "../contexts/problemsDuckyHandlersRef";
import { DEFAULT_BUNDLED_DUCKY_STYLE } from "../generated/bundledDuckies";
import type { ChatTab, EditorLayoutState } from "../types/panel";
import { formatProblemsDraft, type ProblemsDraftPayload } from "../utils/formatProblemsDraft";
import { numberedEntryName } from "../utils/numberedEntryName";
import { openChatBesideFile } from "../utils/openChatBesideFile";
import { enqueueComposerDraft } from "./chatComposerCache";
import { getApi } from "./usePanelApi";

interface UseProblemsToDuckyHandlersOptions {
  allChats: ChatTab[];
  layout: EditorLayoutState;
  activeFileTabId?: string;
  activeFilePath?: string;
  openTab: (tab: import("../types/panel").EditorTab) => void;
  setLayout: (updater: (prev: EditorLayoutState) => EditorLayoutState) => void;
  activateTabInGroup: (groupId: string, tabId: string) => void;
  setFocusedGroup: (groupId: string) => void;
  reloadChats?: () => void | Promise<void>;
}

export function useProblemsToDuckyHandlers({
  allChats,
  layout,
  activeFileTabId,
  activeFilePath,
  openTab,
  setLayout,
  activateTabInGroup,
  setFocusedGroup,
  reloadChats,
}: UseProblemsToDuckyHandlersOptions): ProblemsDuckyHandlers {
  const openChatWithDraft = useCallback(
    (chat: ChatTab, payload: ProblemsDraftPayload) => {
      enqueueComposerDraft(chat.id, formatProblemsDraft(payload));
      openChatBesideFile({
        chat,
        layout,
        fileTabId: activeFileTabId,
        openTab,
        setLayout,
        activateTabInGroup,
        setFocusedGroup,
      });
    },
    [layout, activeFileTabId, openTab, setLayout, activateTabInGroup, setFocusedGroup],
  );

  const onSend = useCallback(
    (chatId: string, payload: ProblemsDraftPayload) => {
      const chat = allChats.find((c) => c.id === chatId);
      if (!chat) return;
      openChatWithDraft(chat, payload);
    },
    [allChats, openChatWithDraft],
  );

  const onCreateAndSend = useCallback(
    async (payload: ProblemsDraftPayload) => {
      const api = getApi();
      if (!api) return;

      const title = numberedEntryName(
        "NewDucky",
        allChats.map((c) => c.name),
      );
      const normFile = activeFilePath?.replace(/\\/g, "/");
      const conv = await api.create_conversation("", DEFAULT_BUNDLED_DUCKY_STYLE, normFile);
      if (conv.title !== title) {
        await api.rename_conversation(conv.id, title);
      }

      const chat: ChatTab = {
        id: conv.id,
        name: title,
        duckyStyle: conv.ducky_style || DEFAULT_BUNDLED_DUCKY_STYLE,
        filePath: conv.file_path?.replace(/\\/g, "/") || normFile,
      };
      openChatWithDraft(chat, payload);
      await reloadChats?.();
    },
    [activeFilePath, allChats, openChatWithDraft, reloadChats],
  );

  return useMemo(
    () => ({
      chats: allChats.map((c) => ({ id: c.id, name: c.name, duckyStyle: c.duckyStyle })),
      onSend,
      onCreateAndSend,
    }),
    [allChats, onSend, onCreateAndSend],
  );
}
