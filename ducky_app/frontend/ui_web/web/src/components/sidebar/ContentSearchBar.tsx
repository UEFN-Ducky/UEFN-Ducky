import { useEffect, useRef } from "react";
import { Icons } from "../../icons/Icons";
import type { SidebarTextSearchState } from "../../hooks/useSidebarTextSearch";
import { SidebarTextSearchControls } from "./SidebarTextSearchControls";

export type PanelSearchMode = "filter" | "text";

interface ContentSearchBarProps {
  mode: PanelSearchMode;
  onModeChange: (mode: PanelSearchMode) => void;
  filterLabel: string;
  filterPlaceholder: string;
  filterQuery: string;
  onFilterQueryChange: (query: string) => void;
  onClose: () => void;
  textSearch: SidebarTextSearchState;
}

export function ContentSearchBar({
  mode,
  onModeChange,
  filterLabel,
  filterPlaceholder,
  filterQuery,
  onFilterQueryChange,
  onClose,
  textSearch,
}: ContentSearchBarProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const t = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, [mode]);

  return (
    <div className="content-search-bar no-drag">
      <div className="content-search-mode-toggle" role="group" aria-label="Search mode">
        <button
          type="button"
          className={`content-search-mode-btn ${mode === "filter" ? "is-active" : ""}`}
          aria-pressed={mode === "filter"}
          onClick={() => onModeChange("filter")}
        >
          {filterLabel}
        </button>
        <button
          type="button"
          className={`content-search-mode-btn ${mode === "text" ? "is-active" : ""}`}
          aria-pressed={mode === "text"}
          onClick={() => onModeChange("text")}
        >
          Text
        </button>
      </div>

      {mode === "filter" ? (
        <div className="file-tree-filter-input-wrap">
          <Icons.Search />
          <input
            ref={inputRef}
            type="search"
            className="file-tree-filter-input selectable-text"
            placeholder={filterPlaceholder}
            value={filterQuery}
            onChange={(e) => onFilterQueryChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                if (filterQuery) onFilterQueryChange("");
                else onClose();
              }
            }}
          />
          {filterQuery ? (
            <button
              type="button"
              className="file-tree-filter-clear"
              title="Clear filter"
              aria-label="Clear filter"
              onClick={() => onFilterQueryChange("")}
            >
              <Icons.InputClear />
            </button>
          ) : null}
        </div>
      ) : (
        <SidebarTextSearchControls search={textSearch} />
      )}
    </div>
  );
}
