import { useEffect, useMemo, useState } from "react";
import type { ChatTab } from "../types/panel";
import { ensureDucktactoeBoardChat } from "./ducktactoeBoardChat";
import { DucktactoeChatShell } from "./DucktactoeChatShell";

export type DucktactoeChatPopupProps = {
  tabId: string;
  allChats: ChatTab[];
  runningChatIds: Set<string>;
  onOpenChat: (chat: ChatTab) => void;
  onOpenFile?: (path: string, name: string, options?: { line?: number }) => void;
  onOpenPlan?: (chatId: string, title?: string) => void;
  onDismissChatAlert?: (chatId: string) => void;
};

/**
 * Legacy plugin-tab entry: redirect path still mounts the chat-first shell
 * (board aside + ChatPane) so old `plugin:ducktactoe:board` tabs keep working.
 */
export function DucktactoeChatPopup({
  tabId,
  allChats,
  runningChatIds,
  onOpenChat,
  onOpenFile,
  onOpenPlan,
  onDismissChatAlert,
}: DucktactoeChatPopupProps) {
  const [bound, setBound] = useState<ChatTab | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    void ensureDucktactoeBoardChat(tabId)
      .then((chat) => {
        if (cancelled) return;
        if (!chat) {
          setError("Could not create Duck-Tac-Toe chat.");
          return;
        }
        setBound(chat);
        // Prefer the real chat tab so the board lives inside chat, not a plugin tab.
        onOpenChat(chat);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [tabId, onOpenChat]);

  const chat = useMemo(() => {
    if (!bound) return null;
    return allChats.find((c) => c.id === bound.id) ?? bound;
  }, [allChats, bound]);

  if (error) {
    return (
      <div className="ducktactoe-chat-dock ducktactoe-chat-dock--error" role="alert">
        {error}
      </div>
    );
  }

  if (!chat) {
    return (
      <div className="ducktactoe-chat-dock ducktactoe-chat-dock--loading" aria-busy="true">
        Opening Duck-Tac-Toe chat…
      </div>
    );
  }

  return (
    <DucktactoeChatShell
      chat={chat}
      allChats={allChats}
      runningChatIds={runningChatIds}
      variant="popup"
      onOpenChat={onOpenChat}
      onOpenFile={onOpenFile}
      onOpenPlan={onOpenPlan}
      onDismissChatAlert={onDismissChatAlert}
    />
  );
}
