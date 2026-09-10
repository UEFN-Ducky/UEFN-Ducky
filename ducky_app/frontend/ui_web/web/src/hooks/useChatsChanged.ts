import { useEffect } from "react";
import type { AgentEvent } from "../types/panel";
import { installAgentEventBus, subscribeAgentEvents } from "./useAgentEventBus";

export interface RemoteConversation {
  id: string;
  title: string;
  folder_id?: string;
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
      if (event.conv_id) {
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
