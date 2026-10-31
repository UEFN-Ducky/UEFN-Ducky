import { useCallback, useRef, type ReactNode } from "react";

import { SplitResizeHandle } from "./SplitResizeHandle";

import { SplitCornerHandles, type CornerResizeOp } from "./SplitCornerHandles";

import { EditorGroupPane } from "./EditorGroupPane";

import { ScopedCss, useScopedClass } from "../utils/scopedCss";

import type {
  ChatTab,
  EditorLayoutNode,
  EditorLayoutState,
  EditorTab,
  FolderItem,
} from "../types/panel";

import type { EditorDropZone } from "../types/panel";

import { MIN_SPLIT_PANE_SIZE, resizeSplitPair } from "../utils/editorLayoutOps";

import { liveSplitFlex, splitNodeById } from "../utils/liveSplitResize";

interface SplitEditorLayoutProps {

  layout: EditorLayoutState;

  openTabs: EditorTab[];

  onLayoutChange: (layout: EditorLayoutState) => void;

  hiddenChatTabs: ChatTab[];

  hiddenFileTabs: EditorTab[];

  hiddenTerminalTabs: EditorTab[];

  onRestartTerminal?: (tab: EditorTab) => void;

  allChats: ChatTab[];

  folders?: FolderItem[];

  contextFilePath?: string;

  runningChatIds: Set<string>;

  onOpenChat: (chat: ChatTab) => void;

  onOpenFile?: (path: string, name: string, options?: { line?: number }) => void;

  onOpenPlan?: (chatId: string, title?: string) => void;

  onDismissChatAlert?: (chatId: string) => void;

  onCloseTab: (tabId: string) => void;

  onReorderTabs: (
    groupId: string,
    draggedId: string,
    targetId: string,
    insertBefore: boolean,
    sourceGroupId?: string,
  ) => void;

  onDropTab: (targetGroupId: string, tabId: string, sourceGroupId: string, zone: EditorDropZone) => void;

  onFocusGroup: (groupId: string) => void;

  onActivateTab: (groupId: string, tabId: string) => void;

  onFocusTab?: (tab: EditorTab, at?: { screenX: number; screenY: number }) => void;

  onSaveTab?: (tab: EditorTab) => void;

  onTabSelect?: (tabId: string) => void;

  onTabActivated?: (tab: EditorTab) => void;

  onPromoteTab?: (tabId: string) => void;

  onRevealFileInSidebar?: (path: string) => void;

  onRevealFileInExplorer?: (path: string) => void;

  completionAlertChatIds?: ReadonlySet<string>;

  variant?: "default" | "focus";

  onToggleGroupLock?: (groupId: string) => void;

}



function SplitEditorPane({

  flexGrow,

  minSize,

  children,

}: {

  flexGrow: number;

  minSize: number;

  children: ReactNode;

}) {

  const paneScopeClass = useScopedClass("split-editor-pane");

  return (

    <div className={`split-editor-pane ${paneScopeClass}`}>

      <ScopedCss

        selector={`.${paneScopeClass}`}

        rules={{

          "--split-pane-flex": `${flexGrow} 1 0%`,

          "--split-pane-min-width": `${minSize}px`,

          "--split-pane-min-height": `${minSize}px`,

        }}

      />

      {children}

    </div>

  );

}



export function SplitEditorLayout({

  layout,

  openTabs,

  onLayoutChange,

  hiddenChatTabs: _hiddenChatTabs,

  hiddenFileTabs: _hiddenFileTabs,

  hiddenTerminalTabs: _hiddenTerminalTabs,

  onRestartTerminal,

  allChats,

  folders = [],

  contextFilePath,

  runningChatIds,

  onOpenChat,

  onOpenFile,

  onOpenPlan,

  onDismissChatAlert,

  onCloseTab,

  onReorderTabs,

  onDropTab,

  onFocusGroup,

  onActivateTab,

  onFocusTab,

  onSaveTab,

  onTabSelect,

  onTabActivated,

  onPromoteTab,

  onRevealFileInSidebar,

  onRevealFileInExplorer,

  completionAlertChatIds,

  variant = "default",

  onToggleGroupLock,

}: SplitEditorLayoutProps) {

  const containerRef = useRef<HTMLDivElement>(null);

  const minPaneSize = MIN_SPLIT_PANE_SIZE;



  // A divider drag moves its two panes straight on the page each frame and hands the layout

  // over once, on release: a layout change per frame re-rendered every pane (~27 ms a move),

  // so the divider fell behind the pointer, most of all side by side where text re-wraps. It

  // also measures the two panes themselves: the whole editor's width made a divider between

  // two of three columns move slower than the pointer (Oct 31 2026).

  const liveResizeRef = useRef<{ start: EditorLayoutState; next: EditorLayoutState; delta: number; pairPx: number;

    panes: HTMLElement[] } | null>(null);



  const startResize = useCallback((splitId: string, childIndex: number, axis: "row" | "column") => {

    const handle = containerRef.current?.querySelector<HTMLElement>(

      `.split-resize-handle[data-split-id="${CSS.escape(splitId)}"][data-child-index="${childIndex}"]`);

    const panes = [handle?.previousElementSibling, handle?.nextElementSibling]

      .filter((el): el is HTMLElement => el instanceof HTMLElement);

    const pairPx = panes.reduce((sum, el) => sum + (axis === "row" ? el.offsetWidth : el.offsetHeight), 0);

    liveResizeRef.current = { start: layout, next: layout, delta: 0, pairPx, panes };

  }, [layout]);



  const moveResize = useCallback((splitId: string, childIndex: number, deltaPx: number) => {

    const live = liveResizeRef.current;

    if (!live || live.pairPx <= 0 || live.panes.length !== 2) return;

    live.delta += deltaPx;

    const node = splitNodeById(live.start.root, splitId);

    const pairWeight = node ? (node.sizes[childIndex] ?? 1) + (node.sizes[childIndex + 1] ?? 1) : 1;

    live.next = resizeSplitPair(live.start, splitId, childIndex, live.delta / live.pairPx,

      (minPaneSize / live.pairPx) * pairWeight);

    const flex = liveSplitFlex(live.next.root, splitId, childIndex);

    if (!flex) return;

    live.panes[0].style.setProperty("--split-pane-flex", flex[0]);

    live.panes[1].style.setProperty("--split-pane-flex", flex[1]);

  }, [minPaneSize]);



  const endResize = useCallback(() => {

    const live = liveResizeRef.current;

    liveResizeRef.current = null;

    if (!live) return;

    if (live.next !== live.start) onLayoutChange(live.next);

    // The layout's own sizes take over once React has drawn them.

    requestAnimationFrame(() => live.panes.forEach((el) => el.style.removeProperty("--split-pane-flex")));

  }, [onLayoutChange]);



  const resizeCorner = useCallback(

    (ops: CornerResizeOp[]) => {

      const container = containerRef.current;

      if (!container) return;

      let next = layout;

      for (const op of ops) {

        const total = op.axis === "row" ? container.clientWidth : container.clientHeight;

        if (total <= 0) continue;

        next = resizeSplitPair(next, op.splitId, op.childIndex, op.deltaPx / total, minPaneSize / total);

      }

      if (next !== layout) onLayoutChange(next);

    },

    [layout, onLayoutChange],

  );



  const renderNode = (node: EditorLayoutNode): ReactNode => {

    if (node.type === "group") {

      const group = layout.groups[node.groupId];

      if (!group) return null;

      return (

        <EditorGroupPane

          group={group}

          openTabs={openTabs}

          isFocused={layout.focusedGroupId === group.id}

          split={layout.root.type !== "group"}

          variant={variant}

          allChats={allChats}

          folders={folders}

          contextFilePath={contextFilePath}

          runningChatIds={runningChatIds}

          onOpenChat={onOpenChat}

          onOpenFile={onOpenFile}

          onOpenPlan={onOpenPlan}

          onDismissChatAlert={onDismissChatAlert}

          onFocusGroup={onFocusGroup}

          onActivateTab={onActivateTab}

          onCloseTab={onCloseTab}

          onReorderTabs={onReorderTabs}

          onDropTab={onDropTab}

          onFocusTab={onFocusTab}

          onSaveTab={onSaveTab}

          onTabSelect={onTabSelect}

          onTabActivated={onTabActivated}

          onPromoteTab={onPromoteTab}

          onRevealFileInSidebar={onRevealFileInSidebar}

          onRevealFileInExplorer={onRevealFileInExplorer}

          completionAlertChatIds={completionAlertChatIds}

          onRestartTerminal={onRestartTerminal}

          onToggleGroupLock={onToggleGroupLock}

        />

      );

    }



    const isRow = node.axis === "row";

    const totalWeight = node.sizes.reduce((a, b) => a + b, 0) || node.children.length;

    const className = isRow ? "split-layout-row" : "split-layout-column";



    return (

      <div className={className}>

        {node.children.map((child, idx) => {

          const weight = node.sizes[idx] ?? 1;

          const flexGrow = weight / totalWeight;

          const nodes: ReactNode[] = [

            <SplitEditorPane key={child.type === "group" ? child.groupId : child.id} flexGrow={flexGrow} minSize={minPaneSize}>

              {renderNode(child)}

            </SplitEditorPane>,

          ];

          if (idx < node.children.length - 1) {

            nodes.push(

              <SplitResizeHandle

                key={`resize-${node.id}-${idx}`}

                orientation={isRow ? "horizontal" : "vertical"}

                splitId={node.id}

                childIndex={idx}

                splitAxis={node.axis}

                onDragStart={() => startResize(node.id, idx, node.axis)}

                onDrag={(delta) => moveResize(node.id, idx, delta)}

                onDragEnd={endResize}

              />,

            );

          }

          return nodes;

        })}

      </div>

    );

  };



  return (

    <>

      <div ref={containerRef} className="split-layout-root">

        {renderNode(layout.root)}

        <SplitCornerHandles containerRef={containerRef} layout={layout} onResizeMany={resizeCorner} />

      </div>

      {/* Hidden chat/file tabs stay unmounted — ChatPane restores from chatMessagesCache
          and FileEditorPane remounts Monaco/LSP on activate. Keeps session memory bounded. */}

    </>

  );

}

