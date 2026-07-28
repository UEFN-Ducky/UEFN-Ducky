import { useEffect } from "react";
import type { AgentEvent } from "../types/panel";
import { installAgentEventBus, subscribeAgentEvents } from "./useAgentEventBus";
import { getApi } from "./usePanelApi";

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
      // #region agent log
      getApi()?.report_ui_perf([{ kind: "dbg_ui_event", name: "chats_changed_reload", duration_ms: 0, conv_id: event.conv_id ?? "" }]);
      // #endregion
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
