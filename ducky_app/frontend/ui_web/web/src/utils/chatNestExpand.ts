/** Persist sidebar expand/collapse for nested chats under a parent row. */

const PREFIX = "uefn-chat-nest:";

export function chatNestDefaultExpanded(_childrenNested: boolean): boolean {
  // Nested stacks (legacy or group) start expanded — subagent collapse retired.
  return true;
}

export function loadChatNestExpanded(chatId: string, defaultExpanded: boolean): boolean {
  try {
    const raw = localStorage.getItem(`${PREFIX}${chatId}`);
    if (raw === "1") return true;
    if (raw === "0") return false;
  } catch {
    /* ignore */
  }
  return defaultExpanded;
}

export function saveChatNestExpanded(chatId: string, expanded: boolean): void {
  try {
    localStorage.setItem(`${PREFIX}${chatId}`, expanded ? "1" : "0");
  } catch {
    /* ignore */
  }
}

/** Subagents retired — composer is never locked for nesting. */
export function isSubagentComposerLocked(_chat: { isSubagent?: boolean }): boolean {
  return false;
}
