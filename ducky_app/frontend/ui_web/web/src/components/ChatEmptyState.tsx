import { DuckyAvatar } from "./ducky/DuckyAvatars";
import { Icons } from "../icons/Icons";
import { TruncatedText } from "./TruncatedText";
import type { ChatTab, FolderItem } from "../types/panel";
import { formatRelativeTime } from "../utils/formatRelativeTime";

interface ChatEmptyStateProps {
  folders: FolderItem[];
  /** Chats that live outside any folder — ignoring them showed "No duckies yet"
   * while the sidebar clearly listed duckies. */
  rootChats?: ChatTab[];
  onChatSelect: (chat: ChatTab) => void;
  onCreateChat: () => void;
}

/** Bigger avatar for the roomier, centered empty-state cards (maps to ducky-avatar--30). */
const EMPTY_STATE_AVATAR_SIZE = 30;

/** Walk the folder tree and gather every chat, so "Recent" reflects nested folders too. */
function collectChats(folders: FolderItem[]): ChatTab[] {
  const out: ChatTab[] = [];
  for (const folder of folders) {
    out.push(...folder.chats);
    if (folder.children.length > 0) out.push(...collectChats(folder.children));
  }
  return out;
}

export function ChatEmptyState({ folders, rootChats = [], onChatSelect, onCreateChat }: ChatEmptyStateProps) {
  // Only the last 5 recent duckies — no full root/folder dump below.
  const recentChats = [...rootChats, ...collectChats(folders)]
    .sort((a, b) => (b.updated ?? 0) - (a.updated ?? 0))
    .slice(0, 5);
  const hasChats = recentChats.length > 0;

  const heroStyle = recentChats[0]?.duckyStyle;

  const renderChatRow = (chat: ChatTab) => (
    <div
      key={chat.id}
      className="no-drag chat-empty-state-chat-row"
      onClick={() => onChatSelect(chat)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onChatSelect(chat);
        }
      }}
    >
      <div className="chat-empty-state-chat-avatar">
        <DuckyAvatar styleId={chat.duckyStyle} size={EMPTY_STATE_AVATAR_SIZE} />
      </div>
      <div className="chat-empty-state-chat-meta">
        <TruncatedText className="chat-empty-state-chat-name" title={chat.name}>
          {chat.name}
        </TruncatedText>
        {chat.updated ? (
          <span className="chat-empty-state-chat-time">{formatRelativeTime(chat.updated)}</span>
        ) : null}
      </div>
    </div>
  );

  return (
    <div className="app-drag-surface chat-empty-state-root">
      {hasChats ? (
        <div className="chat-empty-state-list-wrap">
          <div className="chat-empty-state-hero">
            <div className="chat-empty-state-hero-ducky">
              <DuckyAvatar styleId={heroStyle} size={56} />
            </div>
            <h2 className="chat-empty-state-list-title">Select a ducky</h2>
            <p className="chat-empty-state-list-subtitle">Pick up a recent ducky or start a new one.</p>
          </div>

          <div className="no-drag chat-empty-state-new-chat-row" onClick={onCreateChat}>
            <div className="chat-empty-state-new-chat-icon">
              <Icons.Plus />
            </div>
            <span className="chat-empty-state-new-chat-label">Start new ducky</span>
          </div>

          <div className="chat-empty-state-folder-block">
            <div className="chat-empty-state-section-label">
              <Icons.Clock />
              <span>Recent</span>
            </div>
            <div className="chat-empty-state-chat-list">
              {recentChats.map((chat) => renderChatRow(chat))}
            </div>
          </div>
        </div>
      ) : (
        <div className="chat-empty-state-no-chats">
          <div className="chat-empty-state-hero-ducky">
            <DuckyAvatar size={56} />
          </div>
          <h2 className="chat-empty-state-no-chats-title">No duckies yet</h2>
          <p className="chat-empty-state-no-chats-desc">
            Press <strong>+</strong> in the sidebar, or start one right here.
          </p>
          <button type="button" className="no-drag settings-btn" onClick={onCreateChat}>
            Create ducky
          </button>
        </div>
      )}
    </div>
  );
}
