import { useRef, useState, type RefObject } from "react";
import { DropdownPanel } from "../DropdownPanel";
import { DuckyAvatar, DUCKY_AVATAR_SIZES } from "../ducky/DuckyAvatars";
import { Icons } from "../../icons/Icons";
import type { FolderItem } from "../../types/panel";
import { duckyNameMatches } from "../../utils/duckyTreeFilter";
import { TruncatedText } from "../TruncatedText";

// The archive dropdown sits just below the modal layer (modal-backdrop is 100001)
// so a confirm dialog raised from here — "Delete permanently?" — draws on top of it
// instead of behind. (DropdownPanel defaults to 100010, above modals.)
const ARCHIVE_DROPDOWN_Z_INDEX = 100000;

interface DuckyArchiveDropdownProps {
  archiveChats: FolderItem["chats"];
  activeChats: string[];
  runningChatIds: Set<string>;
  completionAlertChatIds?: ReadonlySet<string>;
  filterQuery?: string;
  onChatSelect: (chat: { id: string; name: string }) => void;
  /** Un-archive: move the ducky back out of the Archive folder to the active root. */
  onReturnToActive: (id: string, name: string) => void;
  onDeleteArchivedChat: (id: string, name: string) => void;
  buttonRef?: RefObject<HTMLButtonElement>;
}

export function DuckyArchiveDropdown({
  archiveChats,
  activeChats,
  runningChatIds,
  completionAlertChatIds,
  filterQuery = "",
  onChatSelect,
  onReturnToActive,
  onDeleteArchivedChat,
  buttonRef: buttonRefProp,
}: DuckyArchiveDropdownProps) {
  const [open, setOpen] = useState(false);
  const localButtonRef = useRef<HTMLButtonElement>(null);
  const buttonRef = buttonRefProp ?? localButtonRef;
  const filtering = Boolean(filterQuery.trim());
  const visibleChats = filtering
    ? archiveChats.filter((chat) => duckyNameMatches(filterQuery, chat.name))
    : archiveChats;
  const archiveCount = archiveChats.length;
  const archiveBadge =
    archiveCount > 0 ? (archiveCount > 9 ? "9+" : String(archiveCount)) : null;

  const close = () => setOpen(false);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className={`icon-btn${open ? " is-active" : ""}${archiveBadge ? " has-badge" : ""}`}
        title={archiveCount > 0 ? `Archive (${archiveCount})` : "Archive"}
        aria-label={archiveCount > 0 ? `Archive (${archiveCount})` : "Archive"}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Icons.Trash />
        {archiveBadge ? <span className="icon-btn-badge">{archiveBadge}</span> : null}
      </button>

      <DropdownPanel
        anchorRef={buttonRef}
        open={open}
        onClose={close}
        minWidth={260}
        width={280}
        zIndex={ARCHIVE_DROPDOWN_Z_INDEX}
      >
        <div className="sidebar-archive-dropdown">
          <div className="sidebar-archive-dropdown-title">Archive</div>
          {visibleChats.length === 0 ? (
            <div className="ui-status-sidebar-muted sidebar-archive-dropdown-empty">
              {filtering ? "No archived duckies match" : "No archived duckies"}
            </div>
          ) : (
            <ul className="sidebar-archive-dropdown-list">
              {visibleChats.map((chat) => {
                const isActive = activeChats.includes(chat.id);
                const isRunning = runningChatIds.has(chat.id);
                const hasCompletionAlert = completionAlertChatIds?.has(chat.id) ?? false;

                return (
                  <li key={chat.id}>
                    <button
                      type="button"
                      className={`sidebar-archive-dropdown-item${isActive ? " is-active" : ""}`}
                      onClick={() => {
                        onChatSelect(chat);
                        close();
                      }}
                    >
                      <span
                        className={`sidebar-archive-dropdown-item-icon${hasCompletionAlert && !isRunning ? " chat-completion-alert" : ""}`}
                      >
                        {isRunning ? (
                          <span className="sidebar-agent-spinner" title="Agent working" />
                        ) : (
                          <DuckyAvatar
                            styleId={chat.duckyStyle}
                            size={DUCKY_AVATAR_SIZES.sidebar}
                            className="ducky-avatar--sidebar"
                          />
                        )}
                      </span>
                      <TruncatedText className="sidebar-archive-dropdown-item-label">{chat.name}</TruncatedText>
                      <div className="sidebar-hover-actions">
                        <button
                          type="button"
                          className="sidebar-action-btn"
                          title="Return to active"
                          aria-label="Return to active"
                          onClick={(e) => {
                            e.stopPropagation();
                            onReturnToActive(chat.id, chat.name);
                          }}
                        >
                          <Icons.Restore />
                        </button>
                        <button
                          type="button"
                          className="sidebar-action-btn sidebar-delete-btn"
                          title="Delete permanently"
                          aria-label="Delete permanently"
                          onClick={(e) => {
                            e.stopPropagation();
                            onDeleteArchivedChat(chat.id, chat.name);
                          }}
                        >
                          <Icons.Trash />
                        </button>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </DropdownPanel>
    </>
  );
}
