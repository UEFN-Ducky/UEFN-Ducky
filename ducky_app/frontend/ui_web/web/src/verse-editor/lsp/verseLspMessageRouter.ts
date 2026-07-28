/** Parsed JSON-RPC message from verse-lsp (subset used by the panel client). */
export type LspJsonRpcMessage = {
  jsonrpc?: string;
  id?: number;
  method?: string;
  params?: unknown;
  result?: unknown;
  error?: { message?: string; code?: number };
};

export type LspMessageKind = "serverRequest" | "response" | "notification";

/** Classify an inbound LSP WebSocket frame. */
export function classifyLspMessage(msg: LspJsonRpcMessage): LspMessageKind {
  if (msg.method) {
    return msg.id !== undefined ? "serverRequest" : "notification";
  }
  if (msg.id !== undefined) {
    return "response";
  }
  return "notification";
}

/** Build result for a server→client request (void methods use null). */
export function handleServerRequestResult(
  method: string,
  params: unknown,
): unknown {
  switch (method) {
    case "client/registerCapability":
    case "client/unregisterCapability":
      return null;

    case "workspace/configuration": {
      const items = (params as { items?: unknown[] } | undefined)?.items;
      if (Array.isArray(items)) {
        return items.map(() => ({}));
      }
      return [];
    }

    case "window/workDoneProgress/create":
      return null;

    case "workspace/applyEdit":
      return { applied: false };

    default:
      return null;
  }
}
