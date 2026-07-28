import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { editor } from "monaco-editor";
import "monaco-editor/esm/vs/base/browser/ui/codicons/codicon/codicon.css";

import { FileEditHistoryContent } from "../../components/FileEditHistoryContent";
import { HistoryDateRangeHeaderButton, useHistoryDateRange } from "../../components/history/HistoryDateRangePicker";
import { SidebarStackedPanels } from "../../components/sidebar/SidebarStackedPanels";
import { SidebarPanelTabs } from "../../components/sidebar/SidebarPanelTabs";
import { SidebarSectionHeader } from "../../components/sidebar/SidebarSectionHeader";
import { useFileHistory } from "../../hooks/useFileHistory";
import { filterHistoryByDateRange } from "../../utils/historyDateRange";
import { useVerseOutlinePanelLayout } from "../../hooks/useVerseOutlinePanelLayout";
import { useVerseOutlinePanelMode } from "../../hooks/useVerseOutlinePanelMode";
import { Icons } from "../../icons/Icons";import { getVerseLspSession } from "../lsp/verseLspSession";
import { resolveOpenDocUri } from "../lsp/resolveOpenDocUri";
import { useMinimapEnabled } from "../../hooks/useMinimapEnabled";
import { useOutlinePanelWidth } from "../../hooks/useOutlinePanelWidth";
import { SplitResizeHandle } from "../../components/SplitResizeHandle";
import { verseLspLogError } from "../lsp/verseLspDebug";
import {
  buildOutlineTree,
  collectAllExpandable,
  collectDefaultExpanded,
  findActiveSymbolPath,
  lspKindToCodicon,
  type OutlineNode,
} from "../outline/outlineSymbolIcons";

const OUTLINE_DEBOUNCE_MS = 600;
const EMPTY_RETRY_DELAY_MS = 1500;
const EMPTY_RETRY_WINDOW_MS = 2000;

type FetchStatus = "waiting" | "loading" | "ready" | "empty" | "error";

function OutlineTreeNode({
  node,
  depth,
  nodeId,
  expandedKeys,
  activeId,
  onToggle,
  onNavigate,
}: {
  node: OutlineNode;
  depth: number;
  nodeId: string;
  expandedKeys: Set<string>;
  activeId: string | null;
  onToggle: (id: string) => void;
  onNavigate: (node: OutlineNode) => void;
}) {
  const hasChildren = node.children.length > 0;
  const expanded = expandedKeys.has(nodeId);
  const isSelected = activeId === nodeId;
  const codicon = lspKindToCodicon(node.kind);

  return (
    <li className="verse-outline-tree-node" role="treeitem" aria-expanded={hasChildren ? expanded : undefined}>
      <div
        className={`verse-outline-row${isSelected ? " is-selected" : ""}`}
        style={{ paddingLeft: `${depth * 8}px` }}
      >
        {hasChildren ? (
          <button
            type="button"
            className={`verse-outline-twistie codicon${expanded ? " codicon-chevron-down" : " codicon-chevron-right"}`}
            aria-label={expanded ? "Collapse" : "Expand"}
            onClick={(e) => {
              e.stopPropagation();
              onToggle(nodeId);
            }}
          />
        ) : (
          <span className="verse-outline-twistie verse-outline-twistie--spacer" aria-hidden />
        )}
        <button
          type="button"
          className="verse-outline-item"
          title={`Line ${node.line}`}
          onMouseDown={(e) => {
            // Keep editor state stable until click runs (VS Code outline pattern).
            e.preventDefault();
          }}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onNavigate(node);
          }}
        >
          <span className="verse-outline-icon-label">
            <span className={`codicon ${codicon} verse-outline-symbol-icon`} aria-hidden />
            <span className="verse-outline-name">{node.name}</span>
          </span>
        </button>
      </div>
      {hasChildren && expanded ? (
        <ul className="verse-outline-tree" role="group">
          {node.children.map((child, index) => (
            <OutlineTreeNode
              key={`${nodeId}/${index}:${child.line}:${child.name}`}
              node={child}
              depth={depth + 1}
              nodeId={`${nodeId}/${index}:${child.line}:${child.name}`}
              expandedKeys={expandedKeys}
              activeId={activeId}
              onToggle={onToggle}
              onNavigate={onNavigate}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function VerseOutlinePanel({
  filePath,
  model,
  editorInstance,
  lspReady,
  open,
  historyRefreshKey = 0,
}: {
  filePath: string;
  model: editor.ITextModel | null;
  editorInstance: editor.IStandaloneCodeEditor | null;
  lspReady: boolean;
  open: boolean;
  historyRefreshKey?: number;
}) {
  const { mode: panelMode } = useVerseOutlinePanelMode();
  const stackedLayout = useVerseOutlinePanelLayout();
  const stackRef = useRef<HTMLDivElement | null>(null);
  const [activeTab, setActiveTab] = useState<"outline" | "history">("outline");
  const [contentVersion, setContentVersion] = useState(0);
  const historyEnabled =
    open &&
    (panelMode === "stacked" ? !stackedLayout.collapsed.history : activeTab === "history");
  const history = useFileHistory(filePath, {
    enabled: historyEnabled,
    refreshKey: historyRefreshKey,
    contentVersion,
  });  const { clearPreview: clearHistoryPreview } = history;
  const historyDateRange = useHistoryDateRange();
  const [nodes, setNodes] = useState<OutlineNode[]>([]);
  const [fetchStatus, setFetchStatus] = useState<FetchStatus>("waiting");
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => new Set());
  const [activeId, setActiveId] = useState<string | null>(null);
  const [isResizing, setIsResizing] = useState(false);
  const { width: panelWidth, onResize, persistWidth } = useOutlinePanelWidth();
  const { enabled: minimapEnabled, toggle: toggleMinimap } = useMinimapEnabled(filePath);
  const fetchGenRef = useRef(0);
  const prevTreeSigRef = useRef("");
  const lspReadyAtRef = useRef(0);
  const emptyRetryUsedRef = useRef(false);
  const nodesRef = useRef<OutlineNode[]>([]);
  nodesRef.current = nodes;

  useEffect(() => {
    if (!model || model.isDisposed()) {
      setContentVersion(0);
      return;
    }
    const sub = model.onDidChangeContent(() => {
      setContentVersion((n) => n + 1);
    });
    return () => sub.dispose();
  }, [model]);

  useEffect(() => {
    if (lspReady) {
      lspReadyAtRef.current = Date.now();
      emptyRetryUsedRef.current = false;
    }
  }, [lspReady]);

  useEffect(() => {
    if (!model || model.isDisposed() || !lspReady) {
      if (!nodesRef.current.length) {
        setNodes([]);
        setFetchStatus(lspReady ? "loading" : "waiting");
      }
      return;
    }

    const session = getVerseLspSession();
    if (!session) {
      setFetchStatus("waiting");
      return;
    }

    const resolved = resolveOpenDocUri(session.projectRoot, filePath);
    if (!resolved) {
      setFetchStatus("waiting");
      return;
    }

    const { docUri } = resolved;
    const gen = ++fetchGenRef.current;
    if (!nodesRef.current.length) {
      setFetchStatus("loading");
    }

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

          const sig = JSON.stringify(tree);
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

    const timer = window.setTimeout(() => runFetch(), OUTLINE_DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timer);
    };
  }, [model, lspReady, contentVersion, filePath]);

  useEffect(() => {
    if (!editorInstance || !nodes.length) {
      setActiveId(null);
      return;
    }

    const syncFromCursor = () => {
      const pos = editorInstance.getPosition();
      if (!pos) return;
      const { activeId: nextId, ancestorIds } = findActiveSymbolPath(nodes, pos.lineNumber);
      setActiveId(nextId);
      if (ancestorIds.length) {
        setExpandedKeys((prev) => {
          const next = new Set(prev);
          for (const id of ancestorIds) next.add(id);
          return next;
        });
      }
    };

    syncFromCursor();
    const sub = editorInstance.onDidChangeCursorPosition(syncFromCursor);
    return () => sub.dispose();
  }, [editorInstance, nodes]);

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
  }, [fetchStatus]);

  const selectTab = useCallback(
    (tab: "outline" | "history") => {
      if (activeTab === "history" && tab !== "history") {
        clearHistoryPreview();
      }
      setActiveTab(tab);
    },
    [activeTab, clearHistoryPreview],
  );

  useEffect(() => {
    if (!open) {
      clearHistoryPreview();
      return;
    }
    if (panelMode === "tabs" && activeTab !== "history") {
      clearHistoryPreview();
      return;
    }
    if (panelMode === "stacked" && stackedLayout.collapsed.history) {
      clearHistoryPreview();
    }
  }, [open, panelMode, activeTab, stackedLayout.collapsed.history, clearHistoryPreview]);

  const outlineActions = useMemo(
    () => (
      <>
        <button
          type="button"
          className={`verse-outline-header-btn${!minimapEnabled ? " is-active" : ""}`}
          title={minimapEnabled ? "Hide minimap" : "Show minimap"}
          aria-label={minimapEnabled ? "Hide minimap" : "Show minimap"}
          aria-pressed={!minimapEnabled}
          onClick={toggleMinimap}
        >
          <Icons.Minimap />
        </button>
        <button
          type="button"
          className="verse-outline-header-btn"
          title="Collapse All"
          aria-label="Collapse All"
          onClick={collapseAll}
          disabled={!nodes.length}
        >
          <Icons.CollapseAll />
        </button>
        <button
          type="button"
          className="verse-outline-header-btn"
          title="Expand All"
          aria-label="Expand All"
          onClick={expandAll}
          disabled={!nodes.length}
        >
          <span className="codicon codicon-list-flat" aria-hidden />
        </button>
      </>
    ),
    [collapseAll, expandAll, minimapEnabled, nodes.length, toggleMinimap],
  );

  const outlineBody = useMemo((): ReactNode => {
    if (fetchStatus !== "ready" && !nodes.length) {
      return <p className="verse-outline-empty">{statusMessage}</p>;
    }
    if (!nodes.length) {
      return <p className="verse-outline-empty">{statusMessage}</p>;
    }
    return (
      <ul className="verse-outline-tree" role="tree">
        {nodes.map((node, index) => {
          const nodeId = `root/${index}:${node.line}:${node.name}`;
          return (
            <OutlineTreeNode
              key={nodeId}
              node={node}
              depth={0}
              nodeId={nodeId}
              expandedKeys={expandedKeys}
              activeId={activeId}
              onToggle={toggleExpanded}
              onNavigate={navigate}
            />
          );
        })}
      </ul>
    );
  }, [
    activeId,
    expandedKeys,
    fetchStatus,
    navigate,
    nodes,
    statusMessage,
    toggleExpanded,
  ]);

  const historyActions = useMemo(
    () => (
      <HistoryDateRangeHeaderButton
        range={historyDateRange.range}
        onChange={historyDateRange.setRange}
        onReset={historyDateRange.reset}
      />
    ),
    [historyDateRange.range, historyDateRange.setRange, historyDateRange.reset],
  );

  const filteredHistoryEntries = useMemo(
    () => filterHistoryByDateRange(history.entries, historyDateRange.range),
    [history.entries, historyDateRange.range],
  );

  const historyBody = (
    <FileEditHistoryContent
      variant="panel"
      entries={filteredHistoryEntries}
      totalEntryCount={history.entries.length}
      loading={history.loading}
      previewEntryId={history.previewEntryId}
      activeEntryId={history.activeEntryId}
      onPreview={(entry) => void history.preview(entry)}
      onClearPreview={history.clearPreview}
      onRestore={(entry) => void history.restore(entry)}
    />
  );
  const handleResize = useCallback(
    (delta: number) => {
      setIsResizing(true);
      onResize(delta);
    },
    [onResize],
  );

  const handleResizeEnd = useCallback(() => {
    setIsResizing(false);
    persistWidth();
  }, [persistWidth]);

  const panelStyle = useMemo(
    (): CSSProperties =>
      ({
        "--verse-outline-width": `${panelWidth}px`,
      }) as CSSProperties,
    [panelWidth],
  );

  return (
    <aside
      className={`verse-outline-panel${open ? " is-open" : ""}${isResizing ? " is-resizing" : ""}`}
      style={panelStyle}
      aria-hidden={!open}
    >
      {open ? (
        <SplitResizeHandle
          className="verse-outline-resize-handle"
          onDrag={handleResize}
          onDragEnd={handleResizeEnd}
          ariaLabel="Resize outline panel"
        />
      ) : null}
      <div className={`verse-outline-panel-inner${panelMode === "stacked" ? " verse-outline-panel-inner--stacked" : ""}`}>
        {panelMode === "tabs" ? (
          <>
            <SidebarPanelTabs
              tabs={[
                { id: "outline" as const, title: "Outline", icon: <Icons.Outline /> },
                { id: "history" as const, title: "History", icon: <Icons.Clock /> },
              ]}
              activeTab={activeTab}
              onSelect={selectTab}
            />
            <div className={`sidebar-panel-content${activeTab !== "outline" ? " sidebar-panel-hidden" : ""}`}>
              <SidebarSectionHeader title="Outline" actions={outlineActions} />
              <div className="verse-outline-body sidebar-panel-scroll">{outlineBody}</div>
            </div>
            <div className={`sidebar-panel-content${activeTab !== "history" ? " sidebar-panel-hidden" : ""}`}>
              <SidebarSectionHeader title="History" actions={historyActions} />
              <div className="verse-outline-body sidebar-panel-scroll">{historyBody}</div>
            </div>
          </>
        ) : (
          <SidebarStackedPanels
            stackRef={stackRef}
            order={stackedLayout.order}
            splitRatio={stackedLayout.splitRatio}
            collapsed={stackedLayout.collapsed}
            onToggleCollapsed={stackedLayout.toggleCollapsed}
            onSwapPanels={stackedLayout.swapPanels}
            onResizeSplit={stackedLayout.resizeSplit}
            onPersistSplit={stackedLayout.persistSplit}
            panels={{
              outline: {
                title: "Outline",
                icon: <Icons.Outline />,
                actions: outlineActions,
                children: <div className="verse-outline-body sidebar-panel-scroll">{outlineBody}</div>,
              },
              history: {
                title: "History",
                icon: <Icons.Clock />,
                actions: historyActions,
                children: <div className="verse-outline-body">{historyBody}</div>,
              },
            }}
          />
        )}
      </div>    </aside>
  );
}
