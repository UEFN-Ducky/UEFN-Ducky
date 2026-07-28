import { Icons } from "../../icons/Icons";
import { DuckyAvatar, DUCKY_AVATAR_SIZES } from "../ducky/DuckyAvatars";
import { FileTypeIcon } from "../../verse-editor/components/FileTypeIcon";
import type { FolderItem } from "../../types/panel";
import type { SidebarTextSearchState } from "../../hooks/useSidebarTextSearch";

function basename(path: string): string {
  return path.split("/").pop() || path;
}

function folderNameById(folders: FolderItem[], id: string): string {
  if (!id) return "";
  const walk = (items: FolderItem[]): string | null => {
    for (const folder of items) {
      if (folder.id === id) return folder.name;
      const nested = walk(folder.children);
      if (nested) return nested;
    }
    return null;
  };
  return walk(folders) ?? "";
}

function highlightPreview(preview: string, query: string, caseSensitive: boolean) {
  if (!query.trim()) return preview;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(${escaped})`, caseSensitive ? "g" : "gi");
  const parts = preview.split(re);
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <mark key={`${part}-${i}`} className="sidebar-search-match">
        {part}
      </mark>
    ) : (
      part
    ),
  );
}

interface SidebarTextSearchResultsProps {
  search: SidebarTextSearchState;
  showFileResults?: boolean;
  showChatResults?: boolean;
}

function emptyStateTarget(showFileResults: boolean, showChatResults: boolean): string {
  if (showFileResults && showChatResults) return "Verse files and chats";
  if (showFileResults) return "Verse files";
  if (showChatResults) return "chats";
  return "your workspace";
}

export function SidebarTextSearchResults({
  search,
  showFileResults = true,
  showChatResults = true,
}: SidebarTextSearchResultsProps) {
  const {
    folders,
    debouncedQuery,
    caseSensitive,
    results,
    summary,
    loading,
    collapsedGroups,
    toggleGroup,
    openFileMatch,
    openChatMatch,
  } = search;

  const showEmptyState = !debouncedQuery && !loading;

  return (
    <>
      {summary ? <div className="sidebar-search-summary">{summary}</div> : null}
      <div className="sidebar-search-results">
        {showEmptyState ? (
          <div className="sidebar-search-empty">
            <span className="sidebar-search-empty-icon" aria-hidden="true">
              <Icons.Search />
            </span>
            <p className="sidebar-search-empty-title">
              Search {emptyStateTarget(showFileResults, showChatResults)}
            </p>
            <p className="sidebar-search-empty-hint">
              Type above to find matches. Use <span className="sidebar-search-empty-kbd">Aa</span> for
              case and <span className="sidebar-search-empty-kbd">ab</span> for whole word.
            </p>
          </div>
        ) : null}
        {showFileResults
          ? results?.file_results.map((group) => {
          const key = `file:${group.path}`;
          const collapsed = collapsedGroups.has(key);
          return (
            <div key={key} className={`sidebar-search-group ${collapsed ? "is-collapsed" : ""}`}>
              <div className="sidebar-search-group-header">
                <button
                  type="button"
                  className="sidebar-search-group-chevron-btn"
                  aria-label={collapsed ? "Expand results" : "Collapse results"}
                  onClick={() => toggleGroup(key)}
                >
                  <span className="sidebar-search-group-chevron">
                    <Icons.ChevronDown />
                  </span>
                </button>
                <button
                  type="button"
                  className="sidebar-search-group-open-btn"
                  title={group.path}
                  onClick={() => {
                    const first = group.matches[0];
                    openFileMatch(group.path, first?.line ?? 1, first?.column ?? 1);
                  }}
                >
                  <span className="sidebar-search-group-icon">
                    <FileTypeIcon path={group.path} size={13} />
                  </span>
                  <span className="sidebar-search-group-label">{basename(group.path)}</span>
                  <span className="sidebar-search-group-badge">{group.matches.length}</span>
                </button>
              </div>
              <div className="sidebar-search-group-matches-clip">
                <div className="sidebar-search-group-matches" aria-hidden={collapsed || undefined}>
                  {group.matches.map((match, idx) => (
                    <button
                      key={`${match.line}-${match.column}-${idx}`}
                      type="button"
                      className="sidebar-search-match-row selectable-text"
                      title={group.path}
                      onClick={() => openFileMatch(group.path, match.line, match.column)}
                    >
                      {match.line}: {highlightPreview(match.preview, debouncedQuery, caseSensitive)}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          );
        })
          : null}

        {showChatResults
          ? results?.chat_results.map((group) => {
          const key = `chat:${group.id}`;
          const collapsed = collapsedGroups.has(key);
          const folderLabel = folderNameById(folders, group.folder_id);
          return (
            <div key={key} className={`sidebar-search-group ${collapsed ? "is-collapsed" : ""}`}>
              <div className="sidebar-search-group-header">
                <button
                  type="button"
                  className="sidebar-search-group-chevron-btn"
                  aria-label={collapsed ? "Expand results" : "Collapse results"}
                  onClick={() => toggleGroup(key)}
                >
                  <span className="sidebar-search-group-chevron">
                    <Icons.ChevronDown />
                  </span>
                </button>
                <button
                  type="button"
                  className="sidebar-search-group-open-btn"
                  title={`${folderLabel} / ${group.title}`}
                  onClick={() => openChatMatch({ id: group.id, name: group.title })}
                >
                  <span className="sidebar-search-group-icon">
                    <DuckyAvatar
                      styleId={group.ducky_style}
                      size={DUCKY_AVATAR_SIZES.sidebar}
                      className="ducky-avatar--sidebar"
                    />
                  </span>
                  <span className="sidebar-search-group-label">{group.title}</span>
                  <span className="sidebar-search-group-badge">{group.matches.length}</span>
                </button>
              </div>
              <div className="sidebar-search-group-matches-clip">
                <div className="sidebar-search-group-matches" aria-hidden={collapsed || undefined}>
                  {group.matches.map((match, idx) => (
                    <button
                      key={`${match.message_id}-${idx}`}
                      type="button"
                      className="sidebar-search-match-row selectable-text"
                      title={group.title}
                      onClick={() => openChatMatch({ id: group.id, name: group.title })}
                    >
                      {highlightPreview(match.preview, debouncedQuery, caseSensitive)}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          );
        })
          : null}
      </div>
    </>
  );
}
