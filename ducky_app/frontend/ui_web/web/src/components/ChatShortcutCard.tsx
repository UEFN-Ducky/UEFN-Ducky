import type { LinkedAgentStatus } from "../types/panel";
import { DuckyAvatar, DUCKY_AVATAR_SIZES } from "./ducky/DuckyAvatars";
import { TruncatedText } from "./TruncatedText";

export type ChatShortcutStatusTone = LinkedAgentStatus | "muted";

interface ChatShortcutCardProps {
  title: string;
  subtitle?: string;
  statusLabel?: string;
  statusTone?: ChatShortcutStatusTone;
  isRunning?: boolean;
  compact?: boolean;
  duckyStyle?: string;
  onOpen: () => void;
  onStop?: () => void;
}

export function ChatShortcutCard({
  title,
  subtitle,
  statusLabel,
  statusTone = "muted",
  isRunning = false,
  compact = false,
  duckyStyle,
  onOpen,
  onStop,
}: ChatShortcutCardProps) {
  const statusDotClass = `chat-shortcut-card-status-dot chat-shortcut-card-status-dot--${statusTone}${
    isRunning ? " chat-shortcut-card-status-dot--running" : ""
  } ${isRunning ? "status-dot online" : "status-dot"}`;

  return (
    <div
      className={`no-drag ${compact ? "chat-shortcut-card--compact" : "chat-shortcut-card"}`}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      <div className="chat-shortcut-card-main">
        {statusLabel ? <div className={statusDotClass} /> : null}
        <span className="chat-shortcut-card-avatar-wrap">
          <DuckyAvatar styleId={duckyStyle} size={DUCKY_AVATAR_SIZES.compact} />
        </span>
        <div className="chat-shortcut-card-text-wrap">
          <TruncatedText className="chat-shortcut-card-title" title={title}>
            {title}
          </TruncatedText>
          {(subtitle || statusLabel) && (
            <TruncatedText
              className="chat-shortcut-card-subtitle"
              title={[subtitle, statusLabel].filter(Boolean).join(" · ")}
            >
              {subtitle}
              {subtitle && statusLabel ? " · " : ""}
              {statusLabel}
            </TruncatedText>
          )}
        </div>
      </div>
      <div className="chat-shortcut-card-actions">
        <button
          type="button"
          className="no-drag chat-shortcut-card-open-btn"
          onClick={(e) => {
            e.stopPropagation();
            onOpen();
          }}
        >
          Open
        </button>
        {isRunning && onStop ? (
          <button
            type="button"
            className="no-drag chat-shortcut-card-stop-btn"
            title="Stop agent"
            onClick={(e) => {
              e.stopPropagation();
              onStop();
            }}
          >
            <div className="chat-shortcut-card-stop-btn-icon" />
          </button>
        ) : null}
      </div>
    </div>
  );
}
