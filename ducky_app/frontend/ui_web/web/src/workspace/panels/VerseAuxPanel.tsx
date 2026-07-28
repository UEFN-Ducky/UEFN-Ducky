import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import type { editor } from "monaco-editor";
import "monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon.css";

import { FileEditHistoryContent } from "../../components/FileEditHistoryContent";
import { useFileHistory } from "../../hooks/useFileHistory";
import { EMPTY_HISTORY_DATE_RANGE, filterHistoryByDateRange, type HistoryDateRange } from "../../utils/historyDateRange";
import { getVerseLspSession } from "../../verse-editor/lsp/verseLspSession";
import { resolveOpenDocUri } from "../../verse-editor/lsp/resolveOpenDocUri";
import { verseLspLogError } from "../../verse-editor/lsp/verseLspDebug";
import {
  buildOutlineTree,
  collectAllExpandable,
  collectDefaultExpanded,
  filterOutlineByKinds,
  findActiveSymbolPath,
  flattenExpandedRows,
  lspKindToCodicon,
  outlineTreeSignature,
  type OutlineNode,
  type OutlineRow,
} from "../../verse-editor/outline/outlineSymbolIcons";
import { isVerseFile } from "../../verse-editor/utils/isVerseFile";

const OUTLINE_DEBOUNCE_MS = 600;
const EMPTY_RETRY_DELAY_MS = 1500;
const EMPTY_RETRY_WINDOW_MS = 2000;
const OUTLINE_ROW_HEIGHT = 22;
const OUTLINE_OVERSCAN_ROWS = 10;

type FetchStatus = "waiting" | "loading" | "ready" | "empty" | "error";

export type VerseOutlineBodyHandle = {
  collapseAll: () => void;
  expandAll: () => void;
};

function OutlineVirtualRow({
  row,
  top,
  isSelected,
  onToggle,
  onNavigate,
}: {
  row: OutlineRow;
  top: number;
  isSelected: boolean;
  onToggle: (id: string) => void;
  onNavigate: (node: OutlineNode) => void;
}) {
  const { node, id, depth, hasChildren, expanded } = row;
  const codicon = lspKindToCodicon(node.kind);

  return (
    <div
      role="treeitem"
      aria-level={depth + 1}
      aria-expanded={hasChildren ? expanded : undefined}
      className={`verse-outline-row${isSelected ? " is-selected" : ""}`}
      style={{ top: `${top}px`, paddingLeft: `${depth * 8}px` }}
    >
      {hasChildren ? (
        <button
          type="button"
          className={`verse-outline-twistie codicon${expanded ? " codicon-chevron-down" : " codicon-chevron-right"}`}
          aria-label={expanded ? "Collapse" : "Expand"}
          onClick={(e) => {
            e.stopPropagation();
            onToggle(id);
          }}
        />
      ) : (
        <span className="verse-outline-twistie verse-outline-twistie--spacer" aria-hidden />
      )}
      <button
        type="button"
        className="verse-outline-item"
        title={`Line ${node.line}`}
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onNavigate(node);
        }}
      >
        <span className="verse-outline-icon-label">
          <span className={`codicon ${codicon} verse-outline-symbol-icon`} aria-hidden />
          <span className="verse-outline-name" data-no-translate>
            {node.name}
          </span>
        </span>
      </button>
    </div>
  );
}

export const VerseOutlineBody = forwardRef<
  VerseOutlineBodyHandle,
  {
    filePath: string | undefined;
    model: editor.ITextModel | null;
    editorInstance: editor.IStandaloneCodeEditor | null;
    lspReady: boolean;
    enabled?: boolean;
    kindFilter?: ReadonlySet<number> | null;
    onNodesCountChange?: (count: number) => void;
  }
>(function VerseOutlineBody(
  {
    filePath,
    model,
    editorInstance,
    lspReady,
    enabled = true,
    kindFilter = null,
    onNodesCountChange,
  },
  ref,
) {
  const [contentVersion, setContentVersion] = useState(0);
  const [nodes, setNodes] = useState<OutlineNode[]>([]);
  const [fetchStatus, setFetchStatus] = useState<FetchStatus>("waiting");
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => new Set());
  const [activeId, setActiveId] = useState<string | null>(null);
  const fetchGenRef = useRef(0);
  const prevTreeSigRef = useRef("");
  const lspReadyAtRef = useRef(0);
  const emptyRetryUsedRef = useRef(false);
  const nodesRef = useRef<OutlineNode[]>([]);
  nodesRef.current = nodes;

  const versePath = filePath && isVerseFile(filePath) ? filePath : undefined;

  useEffect(() => {
    if (!enabled || !model || model.isDisposed()) {
      setContentVersion(0);
      return;
    }
    // Debounce the state bump itself, not just the fetch — a per-keystroke React
    // update here re-rendered the whole panel on every keypress and starved
    // Monaco's background tokenizer (stale syntax colors until scroll).
    let timer: number | undefined;
    const sub = model.onDidChangeContent(() => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        timer = undefined;
        setContentVersion((n) => n + 1);
      }, OUTLINE_DEBOUNCE_MS);
    });
    return () => {
      sub.dispose();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [enabled, model]);

  useEffect(() => {
    if (!enabled) return;
    if (lspReady) {
      lspReadyAtRef.current = Date.now();
      emptyRetryUsedRef.current = false;
    }
  }, [enabled, lspReady]);

  useEffect(() => {
    if (!enabled || !versePath || !model || model.isDisposed() || !lspReady) {
      if (!enabled) return;
      if (!nodesRef.current.length) {
        setNodes([]);
        setFetchStatus(lspReady && versePath ? "loading" : "waiting");
      }
      return;
    }

    const session = getVerseLspSession();
    if (!session) {
      setFetchStatus("waiting");
      return;
    }

    const resolved = resolveOpenDocUri(session.projectRoot, versePath);
    if (!resolved) {
      setFetchStatus("waiting");
      return;
    }

    const { docUri } = resolved;
    const gen = ++fetchGenRef.current;
    if (!nodesRef.current.length) setFetchStatus("loading");

    const runFetch = (isRetry = false) => {
      void session.client
        .getDocumentSymbols(docUri)
        .then((result: unknown) => {
          if (gen !== fetchGenRef.current) return;
          const tree = buildOutlineTree(result);

          if (!tree.length && !isRetry && !emptyRetryUsedRef.current) {
            const elapsed = Date.now() - lspReadyAtRef.current;
            if (elapsed < EMPTY_RETRY_WINDOW_MS) {
              emptyRetryUsedRef.current = true;
              window.setTimeout(() => {
                if (gen === fetchGenRef.current) runFetch(true);
              }, EMPTY_RETRY_DELAY_MS);
              return;
            }
          }

          setNodes(tree);
          setFetchStatus(tree.length ? "ready" : "empty");

          // Structure-only signature: line-shift edits keep expansion state; only
          // added/removed/renamed symbols reset it to the default expansion.
          const sig = outlineTreeSignature(tree);
          if (sig !== prevTreeSigRef.current) {
            prevTreeSigRef.current = sig;
            setExpandedKeys(collectDefaultExpanded(tree));
          }
        })
        .catch((err) => {
          if (gen !== fetchGenRef.current) return;
          if (err instanceof Error && err.message === "LSP request cancelled") return;
          verseLspLogError("outline", "documentSymbol failed", err);
          setNodes([]);
          setFetchStatus("error");
        });
    };

    // No extra delay here — contentVersion is already debounced at the source, so
    // this fires immediately on mount/file-switch and once per typing pause.
    runFetch();
  }, [enabled, model, lspReady, contentVersion, versePath]);

  useEffect(() => {
    if (!enabled || !editorInstance || !nodes.length) {
      setActiveId(null);
      return;
    }

    const syncFromCursor = () => {
      const pos = editorInstance.getPosition();
      if (!pos) return;
      const { activeId: nextId, ancestorIds } = findActiveSymbolPath(nodes, pos.lineNumber);
      setActiveId((prev) => (prev === nextId ? prev : nextId));
      if (ancestorIds.length) {
        setExpandedKeys((prev) => {
          let changed = false;
          const next = new Set(prev);
          for (const id of ancestorIds) {
            if (!next.has(id)) {
              next.add(id);
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      }
    };

    syncFromCursor();
    const sub = editorInstance.onDidChangeCursorPosition(syncFromCursor);
    return () => sub.dispose();
  }, [enabled, editorInstance, nodes]);

  const toggleExpanded = useCallback((id: string) => {
    setExpandedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const collapseAll = useCallback(() => {
    setExpandedKeys(new Set());
  }, []);

  const expandAll = useCallback(() => {
    setExpandedKeys(collectAllExpandable(nodes));
  }, [nodes]);

  useImperativeHandle(ref, () => ({ collapseAll, expandAll }), [collapseAll, expandAll]);

  const navigate = useCallback(
    (node: OutlineNode) => {
      if (!editorInstance) return;
      editorInstance.setPosition({ lineNumber: node.line, column: 1 });
      editorInstance.revealLineInCenter(node.line);
      editorInstance.focus();
    },
    [editorInstance],
  );

  const statusMessage = useMemo(() => {
    if (!versePath) return "Open a Verse file to see symbols";
    switch (fetchStatus) {
      case "waiting":
        return "Waiting for language server…";
      case "loading":
        return "Loading symbols…";
      case "empty":
        return "No symbols in this file";
      case "error":
        return "Failed to load symbols";
      default:
        return null;
    }
  }, [fetchStatus, versePath]);

  const filteredNodes = useMemo(
    () => filterOutlineByKinds(nodes, kindFilter),
    [nodes, kindFilter],
  );

  useEffect(() => {
    onNodesCountChange?.(filteredNodes.length);
  }, [filteredNodes.length, onNodesCountChange]);

  // Recomputed only when the tree or expansion changes — never on cursor moves.
  const rows = useMemo(
    () => flattenExpandedRows(filteredNodes, expandedKeys),
    [filteredNodes, expandedKeys],
  );
  const hasRows = rows.length > 0;

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [viewport, setViewport] = useState({ top: 0, height: 0 });

  useEffect(() => {
    if (!hasRows) return;
    const el = scrollRef.current;
    if (!el) return;
    const update = () => {
      const top = el.scrollTop;
      const height = el.clientHeight;
      setViewport((prev) => (prev.top === top && prev.height === height ? prev : { top, height }));
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, [hasRows]);

  if (!nodes.length) {
    return <p className="verse-outline-empty">{statusMessage}</p>;
  }

  if (!filteredNodes.length) {
    return <p className="verse-outline-empty">No symbols match the filter</p>;
  }

  const startRow = Math.max(0, Math.floor(viewport.top / OUTLINE_ROW_HEIGHT) - OUTLINE_OVERSCAN_ROWS);
  const endRow = Math.min(
    rows.length,
    Math.ceil((viewport.top + viewport.height) / OUTLINE_ROW_HEIGHT) + OUTLINE_OVERSCAN_ROWS,
  );

  return (
    <div className="verse-outline-scroll" ref={scrollRef}>
      <div
        className="verse-outline-virtual"
        role="tree"
        style={{ height: `${rows.length * OUTLINE_ROW_HEIGHT}px` }}
      >
        {rows.slice(startRow, endRow).map((row, i) => (
          <OutlineVirtualRow
            key={row.id}
            row={row}
            top={(startRow + i) * OUTLINE_ROW_HEIGHT}
            isSelected={activeId === row.id}
            onToggle={toggleExpanded}
            onNavigate={navigate}
          />
        ))}
      </div>
    </div>
  );
});

export function VerseHistoryBody({
  filePath,
  enabled,
  historyRefreshKey = 0,
  contentVersion = 0,
  dateRange = EMPTY_HISTORY_DATE_RANGE,
}: {
  filePath: string | undefined;
  enabled: boolean;
  historyRefreshKey?: number;
  contentVersion?: number;
  dateRange?: HistoryDateRange;
}) {
  const history = useFileHistory(filePath ?? "", {
    enabled: enabled && !!filePath,
    refreshKey: historyRefreshKey,
    contentVersion,
  });

  const filteredEntries = useMemo(
    () => filterHistoryByDateRange(history.entries, dateRange),
    [history.entries, dateRange],
  );

  if (!filePath) {
    return <p className="verse-outline-empty">Open a file to see history</p>;
  }

  return (
    <FileEditHistoryContent
      variant="panel"
      entries={filteredEntries}
      totalEntryCount={history.entries.length}
      loading={history.loading}
      previewEntryId={history.previewEntryId}
      activeEntryId={history.activeEntryId}
      currentPreview={history.currentPreview}
      currentIsUnsaved={history.currentIsUnsaved}
      onPreview={(entry) => void history.preview(entry)}
      onClearPreview={history.clearPreview}
      onRestore={(entry) => void history.restore(entry)}
    />
  );
}
