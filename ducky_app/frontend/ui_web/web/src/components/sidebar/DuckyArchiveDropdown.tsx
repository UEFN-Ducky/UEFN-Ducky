import { useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { DropdownPanel } from "../DropdownPanel";
import { DuckyAvatar, DUCKY_AVATAR_SIZES } from "../ducky/DuckyAvatars";
import { Icons } from "../../icons/Icons";
import type { AgentProfileDto, FolderItem } from "../../types/panel";
import { duckyNameMatches } from "../../utils/duckyTreeFilter";
import { TruncatedText } from "../TruncatedText";

type ArchiveChat = FolderItem["chats"][number];

/** An archived chat and what was archived with it (a group's duckies and nested groups). */
export type ArchiveNode = { chat: ArchiveChat; children: ArchiveNode[] };

/** Archive rows (one list, like the Duckies tree), each group with its duckies and
 *  nested groups under it. */
export function buildArchiveTree(chats: ArchiveChat[]): ArchiveNode[] {
  const ids = new Set(chats.map((c) => c.id));
  const children = new Map<string, ArchiveChat[]>();
  for (const chat of chats) {
    const parent = (chat.parentConvId || "").trim();
    if (parent && parent !== chat.id && ids.has(parent)) {
      const list = children.get(parent);
      if (list) list.push(chat);
      else children.set(parent, [chat]);
    }
  }
  const seen = new Set<string>();
  const node = (chat: ArchiveChat): ArchiveNode => {
    seen.add(chat.id);
    const kids = (children.get(chat.id) ?? []).filter((c) => !seen.has(c.id));
    // Nested groups after the duckies, like the sidebar.
    kids.sort((a, b) => Number(Boolean(a.isGroup)) - Number(Boolean(b.isGroup)));
    return { chat, children: kids.map(node) };
  };
  const out: ArchiveNode[] = [];
  for (const chat of chats) {
    const parent = (chat.parentConvId || "").trim();
    if (parent && parent !== chat.id && ids.has(parent)) continue;
    out.push(node(chat));
  }
  // A parent loop has no root: list it anyway, or the badge counts chats nobody can see.
  for (const chat of chats) if (!seen.has(chat.id)) out.push(node(chat));
  return out;
}

/** Every chat under a node (what Delete permanently and Return to active take along). */
export function archiveNodeCount(node: ArchiveNode): number {
  return node.children.reduce((sum, child) => sum + 1 + archiveNodeCount(child), 0);
}

/** How many archived chats sit under this one (an archived group's duckies and nested groups). */
export function archivedInsideCount(chats: ArchiveChat[], id: string): number {
  const find = (nodes: ArchiveNode[]): number => {
    for (const n of nodes) {
      if (n.chat.id === id) return archiveNodeCount(n);
      const hit = find(n.children);
      if (hit >= 0) return hit;
    }
    return -1;
  };
  return Math.max(0, find(buildArchiveTree(chats)));
}

/** Keep the rows whose name matches, with the groups they sit in. */
function filterNodes(nodes: ArchiveNode[], query: string): ArchiveNode[] {
  const out: ArchiveNode[] = [];
  for (const n of nodes) {
    const kids = filterNodes(n.children, query);
    if (duckyNameMatches(query, n.chat.name)) out.push(n);
    else if (kids.length) out.push({ ...n, children: kids });
  }
  return out;
}

// The archive dropdown sits just below the modal layer (modal-backdrop is 100001)
// so a confirm dialog raised from here — "Delete permanently?" — draws on top of it
// instead of behind. (DropdownPanel defaults to 100010, above modals.)
const ARCHIVE_DROPDOWN_Z_INDEX = 100000;

interface DuckyArchiveDropdownProps {
  archiveChats: FolderItem["chats"];
  archivedAgents?: AgentProfileDto[];
  activeChats: string[];
  runningChatIds: Set<string>;
  completionAlertChatIds?: ReadonlySet<string>;
  filterQuery?: string;
  onChatSelect: (chat: { id: string; name: string }) => void;
  /** Un-archive: a ducky goes back to the active root; a group comes back whole. */
  onReturnToActive: (id: string, name: string) => void;
  onDeleteArchivedChat: (id: string, name: string) => void;
  onOpenAgent?: (profile: AgentProfileDto) => void;
  onReturnAgent?: (profile: AgentProfileDto) => void;
  onDeleteArchivedAgent?: (profile: AgentProfileDto) => void;
  /** Delete everything in Archive (asks first). */
  onEmptyArchive?: () => void;
  buttonRef?: RefObject<HTMLButtonElement>;
}

export function DuckyArchiveDropdown({
  archiveChats,
  archivedAgents = [],
  activeChats,
  runningChatIds,
  completionAlertChatIds,
  filterQuery = "",
  onChatSelect,
  onReturnToActive,
  onDeleteArchivedChat,
  onOpenAgent,
  onReturnAgent,
  onDeleteArchivedAgent,
  onEmptyArchive,
  buttonRef: buttonRefProp,
}: DuckyArchiveDropdownProps) {
  const [open, setOpen] = useState(false);
  /** Opened groups (groups start folded). */
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  const localButtonRef = useRef<HTMLButtonElement>(null);
  const buttonRef = buttonRefProp ?? localButtonRef;
  const filtering = Boolean(filterQuery.trim());
  const archived = buildArchiveTree(archiveChats);
  const nodes = filtering ? filterNodes(archived, filterQuery) : archived;
  const visibleAgents = filtering
    ? archivedAgents.filter((profile) => duckyNameMatches(filterQuery, profile.name))
    : archivedAgents;
  const archiveCount = archiveChats.length + archivedAgents.length;
  const archiveBadge =
    archiveCount > 0 ? (archiveCount > 9 ? "9+" : String(archiveCount)) : null;

  const close = () => setOpen(false);
  const activate = (event: KeyboardEvent, run: () => void) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    run();
  };

  const renderNode = (node: ArchiveNode, depth: number) => {
    const { chat } = node;
    const isActive = activeChats.includes(chat.id);
    const isRunning = runningChatIds.has(chat.id);
    const hasCompletionAlert = completionAlertChatIds?.has(chat.id) ?? false;
    const inside = archiveNodeCount(node);
    const isGroup = Boolean(chat.isGroup) || node.children.length > 0;
    const expanded = filtering || Boolean(openGroups[chat.id]);
    const openChat = () => {
      onChatSelect(chat);
      close();
    };
    return (
      <li key={chat.id}>
        <div
          role="button"
          tabIndex={0}
          className={`sidebar-archive-dropdown-item${isActive ? " is-active" : ""}`}
          style={{ ["--depth" as string]: depth }}
          onClick={openChat}
          onKeyDown={(e) => activate(e, openChat)}
        >
          {isGroup ? (
            <button
              type="button"
              className="sidebar-archive-dropdown-fold"
              aria-expanded={expanded}
              aria-label={`${expanded ? "Hide" : "Show"} what is in ${chat.name}`}
              onClick={(e) => {
                e.stopPropagation();
                setOpenGroups((prev) => ({ ...prev, [chat.id]: !expanded }));
              }}
            >
              {expanded ? <Icons.ChevronDown /> : <Icons.ChevronRight />}
            </button>
          ) : (
            <span className="sidebar-archive-dropdown-fold" aria-hidden />
          )}
          <span
            className={`sidebar-archive-dropdown-item-icon${hasCompletionAlert && !isRunning ? " chat-completion-alert" : ""}`}
          >
            {isRunning ? (
              <span className="sidebar-agent-spinner" title="Agent working" />
            ) : chat.isGroup ? (
              <span className="sidebar-tree-row-icon sidebar-tree-row-icon--group" title="Group">
                <Icons.Users />
              </span>
            ) : (
              <DuckyAvatar styleId={chat.duckyStyle} size={DUCKY_AVATAR_SIZES.sidebar} className="ducky-avatar--sidebar" />
            )}
          </span>
          <TruncatedText className="sidebar-archive-dropdown-item-label">{chat.name}</TruncatedText>
          {isGroup && inside > 0 ? <span className="sidebar-archive-dropdown-count">{inside}</span> : null}
          <div className="sidebar-hover-actions">
            <button
              type="button"
              className="sidebar-action-btn"
              title={isGroup ? "Return the group to active, with everything in it" : "Return to active"}
              aria-label={`Return ${chat.name} to active`}
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
              title={isGroup ? "Delete the group and everything in it permanently" : "Delete permanently"}
              aria-label={`Delete ${chat.name} permanently`}
              onClick={(e) => {
                e.stopPropagation();
                onDeleteArchivedChat(chat.id, chat.name);
              }}
            >
              <Icons.Trash />
            </button>
          </div>
        </div>
        {isGroup && expanded && node.children.length > 0 ? (
          <ul className="sidebar-archive-dropdown-list">{node.children.map((child) => renderNode(child, depth + 1))}</ul>
        ) : null}
      </li>
    );
  };

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
        minWidth={280}
        width={320}
        zIndex={ARCHIVE_DROPDOWN_Z_INDEX}
      >
        <div className="sidebar-archive-dropdown">
          <div className="sidebar-archive-dropdown-title">
            <span>Archive</span>
            {onEmptyArchive && archiveCount > 0 ? (
              <button
                type="button"
                className="sidebar-action-btn sidebar-delete-btn sidebar-archive-dropdown-empty-all"
                title="Delete everything in Archive"
                aria-label="Delete everything in Archive"
                onClick={onEmptyArchive}
              >
                <Icons.Trash />
                <span>Empty</span>
              </button>
            ) : null}
          </div>
          {nodes.length === 0 && visibleAgents.length === 0 ? (
            <div className="ui-status-sidebar-muted sidebar-archive-dropdown-empty">
              {filtering ? "No archived duckies match" : "No archived duckies"}
            </div>
          ) : null}
          {nodes.length > 0 ? (
            <ul className="sidebar-archive-dropdown-list">{nodes.map((node) => renderNode(node, 0))}</ul>
          ) : null}
          {visibleAgents.length > 0 ? (
            <div>
              <div className="sidebar-archive-dropdown-project sidebar-archive-dropdown-project--static">Ducky profiles</div>
              <ul className="sidebar-archive-dropdown-list">
                {visibleAgents.map((profile) => {
                  const openAgent = () => {
                    onOpenAgent?.(profile);
                    close();
                  };
                  return (
                    <li key={profile.id}>
                      <div
                        role="button"
                        tabIndex={0}
                        className="sidebar-archive-dropdown-item"
                        onClick={openAgent}
                        onKeyDown={(e) => activate(e, openAgent)}
                      >
                        <span className="sidebar-archive-dropdown-fold" aria-hidden />
                        <span className="sidebar-archive-dropdown-item-icon">
                          <DuckyAvatar styleId={profile.ducky_style} size={DUCKY_AVATAR_SIZES.sidebar} className="ducky-avatar--sidebar" />
                        </span>
                        <TruncatedText className="sidebar-archive-dropdown-item-label">{profile.name}</TruncatedText>
                        <div className="sidebar-hover-actions">
                          <button
                            type="button"
                            className="sidebar-action-btn"
                            title="Return to active"
                            aria-label={`Return ${profile.name} to active`}
                            onClick={(e) => {
                              e.stopPropagation();
                              onReturnAgent?.(profile);
                            }}
                          >
                            <Icons.Restore />
                          </button>
                          <button
                            type="button"
                            className="sidebar-action-btn sidebar-delete-btn"
                            title="Delete permanently"
                            aria-label={`Delete ${profile.name} permanently`}
                            onClick={(e) => {
                              e.stopPropagation();
                              onDeleteArchivedAgent?.(profile);
                            }}
                          >
                            <Icons.Trash />
                          </button>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : null}
        </div>
      </DropdownPanel>
    </>
  );
}
