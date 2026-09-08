import { useEffect } from "react";
import type { AgentEvent } from "../../types/panel";
import { subscribeAgentEvents } from "../../hooks/useAgentEventBus";
import { getApi } from "../../hooks/usePanelApi";
import { parseEditorBatch } from "../queue/types";
import type { EditorActionQueue } from "../queue/EditorActionQueue";

export function useVerseEditorSync(queue: EditorActionQueue): void {
  useEffect(() => {
    const handler = (event: AgentEvent) => {
      if (event.type === "editor_batch" && event.editor_batch) {
        const batch = parseEditorBatch(event.editor_batch);
        if (batch?.actions?.length) queue.enqueue(batch.actions);
        return;
      }
      if (event.type === "files_reverted" && Array.isArray(event.paths)) {
        // A changeset revert rewrote these files on disk; open buffers must follow.
        const api = getApi();
        if (!api) return;
        for (const path of event.paths) {
          void api
            .read_project_file(path)
            .then((res) => queue.enqueue([{ type: "apply_content", path, text: res.content, force: true }]))
            .catch(() => undefined);
        }
      }
    };
    return subscribeAgentEvents(handler);
  }, [queue]);
}
