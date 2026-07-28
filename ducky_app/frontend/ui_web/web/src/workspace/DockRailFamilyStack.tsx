import { type Ref } from "react";
import type { ChatSidebarHandle, ChatSidebarProps } from "../components/ChatSidebar";
import { ChatSidebar } from "../components/ChatSidebar";
import { VerseAuxDockPanels, type VerseFamilyId, type VerseFamilyPanels } from "./VerseAuxDockPanels";
import { DockRailTabStack } from "./DockRailTabStack";
import { useWorkspaceDock } from "./WorkspaceDockContext";
import { activePanelForList } from "./workspaceDockStorage";
import type { DockDropTarget } from "../utils/dockPanelDrag";
import type { DockPanelId, DockSide } from "./workspaceDockStorage";

export function DockRailFamilyStack({
  side,
  panelIds,
  sidebarProps,
  versePanels,
  sidebarRef,
  onDockDropZoneChange,
  onDockDragChange,
}: {
  side: DockSide;
  panelIds: DockPanelId[];
  sidebarProps: Omit<ChatSidebarProps, "embedded" | "dockSide" | "isSidebarOpen" | "sidebarResizeDisabled">;
  versePanels: VerseFamilyPanels;
  sidebarRef?: Ref<ChatSidebarHandle | null>;
  onDockDropZoneChange?: (target: DockDropTarget) => void;
  onDockDragChange?: (dragging: boolean) => void;
}) {
  const dock = useWorkspaceDock();

  const sidebarIds = panelIds.filter((id): id is "chats" | "files" => id === "chats" || id === "files");
  const verseIds = panelIds.filter(
    (id): id is VerseFamilyId =>
      id === "outline" ||
      id === "history" ||
      id === "tester" ||
      id === "groupchat" ||
      id === "discordhub",
  );
  const hasSidebar = sidebarIds.length > 0;
  const hasVerse = verseIds.length > 0;

  if (!hasSidebar && !hasVerse) return null;

  // Layout is a property of the rail, not of the panel family. Every panel
  // docked on this side shares the side's mode: one tab bar, or a split stack.
  const mode = dock.panelModeForSide(side);

  if (mode === "tabs") {
    // One active panel for the whole rail, derived from the same variant-filtered
    // list the tab bar renders. Both body families receive it so exactly one body
    // is visible — never the focused panel plus each family's first panel.
    const activePanelId = activePanelForList(panelIds, dock.stackForSide(side).focusedPanel);
    const busyByPanel = {
      chats: {
        busy: sidebarProps.runningChatIds.size > 0,
        busyTitle: "Ducky working",
      },
      tester: {
        busy: !!versePanels.tester.busy,
        busyTitle: versePanels.tester.busyTitle,
      },
    };
    return (
      <DockRailTabStack
        side={side}
        panelIds={panelIds}
        activePanelId={activePanelId}
        busyByPanel={busyByPanel}
        onDockDropZoneChange={onDockDropZoneChange}
        onDockDragChange={onDockDragChange}
      >
        {hasSidebar ? (
          <ChatSidebar
            ref={sidebarRef as Ref<ChatSidebarHandle>}
            {...sidebarProps}
            embedded
            bodiesOnly
            dockActivePanelId={activePanelId}
            dockSide={side}
            isSidebarOpen
          />
        ) : null}
        {hasVerse ? (
          <VerseAuxDockPanels
            side={side}
            panelIds={verseIds}
            versePanels={versePanels}
            bodiesOnly
            activePanelId={activePanelId}
          />
        ) : null}
      </DockRailTabStack>
    );
  }

  // Stacked: all panels on the side share one resizable vertical stack.
  if (hasSidebar && hasVerse) {
    return (
      <div className="dock-rail-family-stack">
        <ChatSidebar
          ref={sidebarRef as Ref<ChatSidebarHandle>}
          {...sidebarProps}
          embedded
          unifiedStack
          unifiedStackPanelIds={panelIds}
          unifiedStackVersePanels={versePanels}
          dockSide={side}
          isSidebarOpen
          onDockDropZoneChange={onDockDropZoneChange}
          onDockDragChange={onDockDragChange}
        />
      </div>
    );
  }

  if (hasSidebar) {
    return (
      <ChatSidebar
        ref={sidebarRef as Ref<ChatSidebarHandle>}
        {...sidebarProps}
        embedded
        dockSide={side}
        isSidebarOpen
        onDockDropZoneChange={onDockDropZoneChange}
        onDockDragChange={onDockDragChange}
      />
    );
  }

  return (
    <VerseAuxDockPanels
      side={side}
      panelIds={verseIds}
      versePanels={versePanels}
      onDockDropZoneChange={onDockDropZoneChange}
      onDockDragChange={onDockDragChange}
    />
  );
}
