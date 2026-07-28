import { Icons } from "../../icons/Icons";
import type { SidebarTextSearchState } from "../../hooks/useSidebarTextSearch";

interface SidebarTextSearchControlsProps {
  search: SidebarTextSearchState;
  showRefresh?: boolean;
}

export function SidebarTextSearchControls({ search, showRefresh = false }: SidebarTextSearchControlsProps) {
  const {
    query,
    setQuery,
    replaceWith,
    setReplaceWith,
    scope,
    caseSensitive,
    setCaseSensitive,
    wholeWord,
    setWholeWord,
    clearSearch,
    runSearch,
    runReplaceAll,
    canReplace,
  } = search;

  const showReplace = scope === "files";

  return (
    <div className="sidebar-search-controls">
      {showRefresh ? (
        <div className="sidebar-text-search-toolbar">
          <button
            type="button"
            className="icon-btn"
            title="Refresh search"
            aria-label="Refresh search"
            onClick={() => void runSearch()}
          >
            <Icons.Refresh />
          </button>
        </div>
      ) : null}

      <div className="sidebar-search-input-wrap">
        <input
          type="search"
          className="sidebar-search-input selectable-text"
          placeholder="Search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          spellCheck={false}
        />
        {query ? (
          <button
            type="button"
            className="sidebar-search-input-clear"
            title="Clear search"
            aria-label="Clear search"
            onClick={clearSearch}
          >
            <Icons.InputClear />
          </button>
        ) : null}
        <div className="sidebar-search-toggles">
          <button
            type="button"
            className={`sidebar-search-toggle ${caseSensitive ? "is-active" : ""}`}
            title="Match case"
            aria-label="Match case"
            aria-pressed={caseSensitive}
            onClick={() => setCaseSensitive((v) => !v)}
          >
            Aa
          </button>
          <button
            type="button"
            className={`sidebar-search-toggle ${wholeWord ? "is-active" : ""}`}
            title="Match whole word"
            aria-label="Match whole word"
            aria-pressed={wholeWord}
            onClick={() => setWholeWord((v) => !v)}
          >
            ab
          </button>
        </div>
      </div>

      {showReplace ? (
        <div className="sidebar-search-input-wrap">
          <input
            type="text"
            className="sidebar-search-input selectable-text"
            placeholder="Replace in Verse files"
            value={replaceWith}
            onChange={(e) => setReplaceWith(e.target.value)}
            spellCheck={false}
          />
          <div className="sidebar-search-replace-actions">
            <button
              type="button"
              className="sidebar-search-replace-btn"
              title="Replace all in Verse files"
              aria-label="Replace all in Verse files"
              disabled={!canReplace}
              onClick={() => void runReplaceAll()}
            >
              <Icons.ReplaceAll />
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
