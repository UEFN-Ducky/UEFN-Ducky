import { useCallback, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";

import { DropdownPanel } from "../../components/DropdownPanel";
import { Icons } from "../../icons/Icons";
import { useMinimapEnabled } from "../../hooks/useMinimapEnabled";
import { OUTLINE_KIND_OPTIONS, lspKindToCodicon } from "../../verse-editor/outline/outlineSymbolIcons";
import { useVerseEditorOptional } from "../../verse-editor/VerseEditorProvider";
import { isVerseFile } from "../../verse-editor/utils/isVerseFile";
import { VerseOutlineBody, type VerseOutlineBodyHandle } from "./VerseAuxPanel";

function OutlineHeaderButton({
  title,
  active = false,
  disabled = false,
  pressed,
  buttonRef,
  onClick,
  children,
}: {
  title: string;
  active?: boolean;
  disabled?: boolean;
  pressed?: boolean;
  buttonRef?: RefObject<HTMLButtonElement>;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      ref={buttonRef}
      type="button"
      className={`verse-outline-header-btn${active ? " is-active" : ""}`}
      title={title}
      aria-label={title}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function VerseOutlineHeaderActions({
  filePath,
  nodesCount,
  kindFilter,
  filterOpen,
  filterBtnRef,
  onFilterOpenChange,
  onToggleKind,
  onClearKindFilter,
  onCollapseAll,
  onExpandAll,
}: {
  filePath: string | undefined;
  nodesCount: number;
  kindFilter: ReadonlySet<number> | null;
  filterOpen: boolean;
  filterBtnRef: RefObject<HTMLButtonElement>;
  onFilterOpenChange: (open: boolean) => void;
  onToggleKind: (kind: number) => void;
  onClearKindFilter: () => void;
  onCollapseAll: () => void;
  onExpandAll: () => void;
}) {
  const path = filePath && isVerseFile(filePath) ? filePath : "";
  const { enabled: minimapEnabled, toggle: toggleMinimap } = useMinimapEnabled(path);
  const isFiltered = kindFilter !== null && kindFilter.size > 0;

  const filterTitle = isFiltered
    ? `Filter: ${kindFilter.size} type${kindFilter.size === 1 ? "" : "s"}`
    : "Filter by symbol type";

  return (
    <>
      <OutlineHeaderButton
        buttonRef={filterBtnRef}
        title={filterTitle}
        active={isFiltered}
        pressed={isFiltered}
        onClick={() => onFilterOpenChange(!filterOpen)}
      >
        <Icons.Filter />
      </OutlineHeaderButton>
      <DropdownPanel
        anchorRef={filterBtnRef}
        open={filterOpen}
        onClose={() => onFilterOpenChange(false)}
        minWidth={168}
        width={168}
      >
        <button
          type="button"
          className={`verse-outline-filter-option${!isFiltered ? " is-selected" : ""}`}
          onClick={onClearKindFilter}
        >
          All types
        </button>
        {OUTLINE_KIND_OPTIONS.map(({ kind, label }) => (
          <button
            key={kind}
            type="button"
            className={`verse-outline-filter-option${kindFilter?.has(kind) ? " is-selected" : ""}`}
            onClick={() => onToggleKind(kind)}
          >
            <span className={`codicon ${lspKindToCodicon(kind)}`} aria-hidden />
            {label}
          </button>
        ))}
      </DropdownPanel>
      <OutlineHeaderButton
        title={minimapEnabled ? "Hide minimap" : "Show minimap"}
        active={!minimapEnabled}
        pressed={!minimapEnabled}
        disabled={!path}
        onClick={toggleMinimap}
      >
        <Icons.Minimap />
      </OutlineHeaderButton>
      <OutlineHeaderButton
        title="Collapse all"
        disabled={!nodesCount}
        onClick={onCollapseAll}
      >
        <Icons.CollapseAll />
      </OutlineHeaderButton>
      <OutlineHeaderButton title="Expand all" disabled={!nodesCount} onClick={onExpandAll}>
        <span className="codicon codicon-list-flat" aria-hidden />
      </OutlineHeaderButton>
    </>
  );
}

export function useVerseOutlineDockPanel(versePath: string | undefined, enabled: boolean) {
  const [kindFilter, setKindFilter] = useState<ReadonlySet<number> | null>(null);
  const [filterOpen, setFilterOpen] = useState(false);
  const [nodesCount, setNodesCount] = useState(0);
  const filterBtnRef = useRef<HTMLButtonElement>(null);
  const outlineRef = useRef<VerseOutlineBodyHandle>(null);

  const verseEditor = useVerseEditorOptional();
  const bridge = versePath && verseEditor ? verseEditor.getEditorBridge(versePath) : null;

  const toggleKind = useCallback((kind: number) => {
    setKindFilter((prev) => {
      if (!prev) return new Set([kind]);
      const next = new Set(prev);
      if (next.has(kind)) {
        next.delete(kind);
        return next.size === 0 ? null : next;
      }
      next.add(kind);
      return next;
    });
  }, []);

  const clearKindFilter = useCallback(() => {
    setKindFilter(null);
    setFilterOpen(false);
  }, []);

  const actions = useMemo(
    () => (
      <VerseOutlineHeaderActions
        filePath={versePath}
        nodesCount={nodesCount}
        kindFilter={kindFilter}
        filterOpen={filterOpen}
        filterBtnRef={filterBtnRef}
        onFilterOpenChange={setFilterOpen}
        onToggleKind={toggleKind}
        onClearKindFilter={clearKindFilter}
        onCollapseAll={() => outlineRef.current?.collapseAll()}
        onExpandAll={() => outlineRef.current?.expandAll()}
      />
    ),
    [versePath, nodesCount, kindFilter, filterOpen, toggleKind, clearKindFilter],
  );

  const children = (
    <div className="verse-outline-body">
      <VerseOutlineBody
        ref={outlineRef}
        filePath={versePath}
        model={bridge?.model ?? null}
        editorInstance={bridge?.editor ?? null}
        lspReady={bridge?.lspReady ?? false}
        enabled={enabled}
        kindFilter={kindFilter}
        onNodesCountChange={setNodesCount}
      />
    </div>
  );

  return { actions, children };
}
