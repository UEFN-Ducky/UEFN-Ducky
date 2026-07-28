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



  const resizePair = useCallback(

    (splitId: string, childIndex: number, deltaPx: number, axis: "row" | "column") => {

      const container = containerRef.current;

      if (!container) return;

      const total = axis === "row" ? container.clientWidth : container.clientHeight;

      if (total <= 0) return;

      onLayoutChange(resizeSplitPair(layout, splitId, childIndex, deltaPx / total, minPaneSize / total));

    },

    [layout, onLayoutChange],

  );



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

                onDrag={(delta) => resizePair(node.id, idx, delta, node.axis)}

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

