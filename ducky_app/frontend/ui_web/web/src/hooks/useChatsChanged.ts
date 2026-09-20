import { useEffect } from "react";
import type { AgentEvent } from "../types/panel";
import { installAgentEventBus, subscribeAgentEvents } from "./useAgentEventBus";

export interface RemoteConversation {
  id: string;
  title: string;
  folder_id?: string;
}

/** Sidebar reload is always ok; opening a tab is not (pipeline hubs stay in the list). */
export function shouldOpenCreatedChat(event: AgentEvent): boolean {
  return event.type === "chats_changed" && Boolean(event.conv_id) && event.open !== false;
}

/** Reload sidebar when MCP tools or linked agents create conversations. */
export function useChatsChanged(
  load: () => Promise<void>,
  onCreated?: (conv: RemoteConversation) => void,
) {
  useEffect(() => {
    installAgentEventBus();
    const handler = (event: AgentEvent) => {
      if (event.type !== "chats_changed") return;
      void load();
      if (shouldOpenCreatedChat(event) && event.conv_id) {
        onCreated?.({
          id: event.conv_id,
          title: event.title ?? "New ducky",
          folder_id: event.folder_id,
        });
      }
    };
    return subscribeAgentEvents(handler);
  }, [load, onCreated]);
}
