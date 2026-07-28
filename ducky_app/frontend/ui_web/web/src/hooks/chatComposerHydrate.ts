/** Fill empty composer fields once the folder list hydrates a newly created chat.

When "New ducky" opens a tab before `list_all_conversations` includes it,
EditorGroupPane mounts ChatPane with a stub (no model/agent). After load, the
same chat id gains model/codingAgent — adopt them only while the composer is
still empty so a live user pick is never overwritten.
*/
export function nextComposerFromChat(args: {
  selectedModel: string;
  codingAgent: string;
  thinkingEffort: string;
  chatModel?: string;
  chatCodingAgent?: string;
  chatThinkingEffort?: string;
}): { selectedModel: string; codingAgent: string; thinkingEffort: string } | null {
  const nextModel = (args.chatModel || "").trim();
  if (args.selectedModel.trim() || !nextModel) return null;
  return {
    selectedModel: nextModel,
    codingAgent: (args.chatCodingAgent || "").trim() || "ducky",
    thinkingEffort: (args.chatThinkingEffort || "").trim() || args.thinkingEffort || "off",
  };
}
