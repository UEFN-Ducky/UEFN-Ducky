import { useEffect } from "react";
import type { AgentEvent } from "../../types/panel";
import { subscribeAgentEvents } from "../../hooks/useAgentEventBus";
import { parseEditorBatch } from "../queue/types";
import type { EditorActionQueue } from "../queue/EditorActionQueue";

export function useVerseEditorSync(queue: EditorActionQueue): void {
  useEffect(() => {
    const handler = (event: AgentEvent) => {
      if (event.type === "editor_batch" && event.editor_batch) {
        const batch = parseEditorBatch(event.editor_batch);
        if (batch?.actions?.length) queue.enqueue(batch.actions);
      }
    };
    return subscribeAgentEvents(handler);
  }, [queue]);
}
