import type { ReactNode } from "react";
import { Icons } from "../icons/Icons";
import type { ChatTab } from "../types/panel";
import {
  buildChatListSections,
  folderNameById,
  resolveChatTab,
  type ChatListPayload,
  type ListedChatRef,
} from "../utils/chatList";
import { ScopedCss, useScopedClass } from "../utils/scopedCss";
import { ChatShortcutCard } from "./ChatShortcutCard";

interface ChatListPanelProps {
  data: ChatListPayload;
  allChats: ChatTab[];
  onOpenChat: (chat: ChatTab) => void;
}

function messageLabel(count: number): string {
  if (count === 1) return "1 message";
  return `${count} messages`;
}

function DepthSection({
  depth,
  className,
  children,
}: {
  depth: number;
  className: string;
  children: ReactNode;
}) {
  const scopeClass = useScopedClass("chat-list-depth");
  return (
    <>
      <ScopedCss selector={`.${scopeClass}`} rules={{ "--chat-list-depth": String(depth) }} />
      <div className={`${className} ${scopeClass}`}>{children}</div>
    </>
  );
}

export function ChatListPanel({ data, allChats, onOpenChat }: ChatListPanelProps) {
  const sections = buildChatListSections(data);
  const totalChats = data.conversations.length;

  if (totalChats === 0 && sections.length === 0) {
    return <div className="chat-list-panel-empty">No duckies in this project yet.</div>;
  }

  return (
    <div className="no-drag chat-list-panel-root">
      <div className="chat-list-panel-header">
        {totalChats} ducky{totalChats === 1 ? "" : "ies"} · click to open
      </div>
      {sections.map(({ folder, depth, conversations }) => (
        <div key={folder.id} className="chat-list-panel-folder-section">
          <DepthSection depth={depth} className="chat-list-panel-folder-header">
            <span className="chat-list-panel-folder-icon">
              <Icons.Folder />
            </span>
            {folder.name}
          </DepthSection>
          {conversations.length === 0 ? (
            <DepthSection depth={depth} className="chat-list-panel-folder-empty">
              (empty)
            </DepthSection>
          ) : (
            <DepthSection depth={depth} className="chat-list-panel-folder-chats">
              {conversations.map((conv) => (
                <ChatShortcutCard
                  key={conv.id}
                  compact
                  title={conv.title}
                  subtitle={messageLabel(conv.messageCount)}
                  onOpen={() => onOpenChat(resolveChatTab(conv.id, conv.title, allChats))}
                  duckyStyle={resolveChatTab(conv.id, conv.title, allChats).duckyStyle}
                />
              ))}
            </DepthSection>
          )}
        </div>
      ))}
    </div>
  );
}

interface SingleChatPanelProps {
  chat: ListedChatRef;
  folders?: ChatListPayload["folders"];
  allChats: ChatTab[];
  onOpenChat: (chat: ChatTab) => void;
}

export function SingleChatPanel({ chat, folders = [], allChats, onOpenChat }: SingleChatPanelProps) {
  const folderName = chat.folderId ? folderNameById(folders, chat.folderId) : undefined;
  const subtitleParts: string[] = [];
  if (folderName) subtitleParts.push(folderName);
  if (chat.messageCount !== undefined) subtitleParts.push(messageLabel(chat.messageCount));

  return (
    <ChatShortcutCard
      compact
      title={chat.title}
      subtitle={subtitleParts.join(" · ") || undefined}
      onOpen={() => onOpenChat(resolveChatTab(chat.id, chat.title, allChats))}
      duckyStyle={resolveChatTab(chat.id, chat.title, allChats).duckyStyle}
    />
  );
}
