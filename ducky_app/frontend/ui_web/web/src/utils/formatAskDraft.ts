import type { AskAiPayload } from "../contexts/askAiHandlersRef";

export function formatAskDraft(payload: AskAiPayload): string {
  const lineRef =
    payload.startLine === payload.endLine
      ? `line ${payload.startLine}`
      : `lines ${payload.startLine}–${payload.endLine}`;
  const path = payload.filePath || "selection";
  return `About \`${path}\` (${lineRef}):\n\n\`\`\`verse\n${payload.text}\n\`\`\``;
}
