import { useEffect, useRef, useSyncExternalStore } from "react";

import { subscribeAgentEvents, installAgentEventBus } from "./useAgentEventBus";

import { getApi } from "./usePanelApi";

import { chatTabId } from "../types/panel";

import { WINDOW_ID } from "../tabs/tabRegistryClient";

import { emitAppHook } from "../sfx/appHooks";

import {

  dismissCompletionAlert,

  getCompletionAlertChatIds,

  setCompletionAlert,

  subscribeCompletionAlerts,

} from "../stores/chatCompletionAlerts";



interface UseChatCompletionAlertsOptions {

  runningChatIds: ReadonlySet<string>;

}



export function useCompletionAlertChatIds(): ReadonlySet<string> {

  return useSyncExternalStore(

    subscribeCompletionAlerts,

    getCompletionAlertChatIds,

    getCompletionAlertChatIds,

  );

}



export { dismissCompletionAlert };



function maybeRaiseFocusedWindow(convId: string) {

  // Registry lookup: if ANOTHER window owns this chat tab, focus_tab raises it and
  // activates the tab there; if nobody owns it, this is a no-op (ok=False).

  const api = getApi();

  if (api?.focus_tab) {

    void api.focus_tab(chatTabId(convId), WINDOW_ID);

  }

}



export function useChatCompletionAlerts({

  runningChatIds,

}: UseChatCompletionAlertsOptions) {

  const wasRunningRef = useRef(new Set<string>());



  useEffect(() => {

    installAgentEventBus();

    return subscribeAgentEvents((event) => {

      if (event.type !== "assistant_done" && event.type !== "error") return;

      const convId = event.conv_id?.trim();

      if (!convId) return;



      setCompletionAlert(convId);

      maybeRaiseFocusedWindow(convId);

      emitAppHook(event.type === "error" ? "agent.error" : "agent.done", { chatId: convId });

    });

  }, []);



  useEffect(() => {

    const wasRunning = wasRunningRef.current;



    for (const id of runningChatIds) {

      wasRunning.add(id);

    }



    for (const id of wasRunning) {

      if (runningChatIds.has(id)) continue;

      wasRunning.delete(id);

      setCompletionAlert(id);

    }

  }, [runningChatIds]);

}

