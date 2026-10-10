import { useEffect } from "react";
import type { AgentEvent } from "../types/panel";
import { installAgentEventBus, subscribeAgentEvents } from "./useAgentEventBus";

export interface RemoteConversation {
  id: string;
  title: string;
  folder_id?: string;
}

const RELOAD_DUCKIES_EVENT = "uefn-reload-duckies";

/** Ask the Duckies tree to reload after a group roster change. */
export function requestReloadDuckies(): void {
  window.dispatchEvent(new Event(RELOAD_DUCKIES_EVENT));
}

/** Sidebar reload is always ok; opening a tab is not (pipeline hubs stay in the list). */
export function shouldOpenCreatedChat(event: AgentEvent): boolean {
  return event.type === "chats_changed" && Boolean(event.conv_id) && event.open !== false;
}

/** Reload sidebar when MCP tools or linked agents create conversations. Chats and
 * groups deleted anywhere leave the tree at once (`onRemoved`), before the reload. */
export function useChatsChanged(
  load: () => Promise<void>,
  onCreated?: (conv: RemoteConversation) => void,
  onRemoved?: (convIds: string[], folderIds: string[]) => void,
) {
  useEffect(() => {
    installAgentEventBus();
    const handler = (event: AgentEvent) => {
      if (event.type !== "chats_changed") return;
      const removedConvs = event.removed_conv_ids ?? [];
      const removedFolders = event.removed_folder_ids ?? [];
      if (removedConvs.length || removedFolders.length) onRemoved?.(removedConvs, removedFolders);
      void load();
      if (shouldOpenCreatedChat(event) && event.conv_id) {
        onCreated?.({
          id: event.conv_id,
          title: event.title ?? "New ducky",
          folder_id: event.folder_id,
        });
      }
    };
    const onReload = () => { void load(); };
    window.addEventListener(RELOAD_DUCKIES_EVENT, onReload);
    const unsubscribe = subscribeAgentEvents(handler);
    return () => {
      window.removeEventListener(RELOAD_DUCKIES_EVENT, onReload);
      unsubscribe();
    };
  }, [load, onCreated, onRemoved]);
}
