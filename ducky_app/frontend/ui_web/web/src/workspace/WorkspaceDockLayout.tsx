import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type ReactNode } from "react";
import type { ChatLayoutMode } from "../types/panel";
import type { ChatSidebarHandle, ChatSidebarProps } from "../components/ChatSidebar";
import { useWorkspaceDock } from "./WorkspaceDockContext";
import { DockRail } from "./DockRail";
import { DockRailFamilyStack } from "./DockRailFamilyStack";
import { useVerseOutlineDockPanel } from "./panels/VerseOutlineDockPanel";
import { useVerseHistoryDockPanel } from "./panels/VerseHistoryDockPanel";
import { useTesterDockPanel } from "./panels/TesterDockPanel";
import { requestOpenDiscordTab, setDiscordTabOpen, useDiscordTabOpen } from "../navigation/openDiscordTab";
import { useFocusWindow } from "../hooks/useFocusWindow";
import {
  dockPanelPluginUi,
  pluginContributesDockPanel,
  usePluginContributions,
  type PluginContributions,
} from "../hooks/usePluginContributions";
import { useDiscordUiPrefs } from "../hooks/usePluginUiPrefs";
import { isVerseFile } from "../verse-editor/utils/isVerseFile";
import { Icons } from "../icons/Icons";
import type { DockDropTarget } from "../utils/dockPanelDrag";
import { dockDropTargetSide } from "../utils/dockPanelDrag";
import type { DockPanelId } from "./workspaceDockStorage";
import { PluginWebviewPane } from "../plugin-ui/PluginWebviewPane";
import { pluginUiTabId } from "../plugin-ui/types";

type WorkspaceDockLayoutProps = {
  layoutMode: ChatLayoutMode;
  children: ReactNode;
  activeFilePath?: string;
  historyRefreshKey?: number;
  sidebarProps: Omit<ChatSidebarProps, "embedded" | "dockSide" | "isSidebarOpen" | "sidebarResizeDisabled">;
  variant?: "default" | "focus";
};

function filterPanelsForVariant(
  ids: DockPanelId[],
  variant: "default" | "focus",
  discordTabOpen: boolean,
  discordPluginOn: boolean,
  showDiscordLeft: boolean,
  showDiscordRight: boolean,
  side: "left" | "right",
  testerPluginOn: boolean,
) {
  if (variant === "focus") {
    return ids.filter(
      (id) => id === "outline" || id === "history" || (id === "tester" && testerPluginOn),
    );
  }
  // One Discord view at a time: while the Discord tab is open, the dock panel
  // vanishes from its rail. The dock snapshot is untouched, so closing the tab
  // restores the panel to its exact side/order/size.
  let next = ids;
  const discordAllowedOnSide =
    discordPluginOn && (side === "left" ? showDiscordLeft : showDiscordRight);
  if (discordTabOpen || !discordAllowedOnSide) {
    next = next.filter((id) => id !== "groupchat" && id !== "discordhub");
  }
  if (!testerPluginOn) {
    next = next.filter((id) => id !== "tester");
  }
  return next;
}

function hasSidebarPanels(ids: DockPanelId[]) {
  return ids.some((id) => id === "chats" || id === "files");
}

function hasVerseAuxPanels(ids: DockPanelId[]) {
  return ids.some(isVerseFamilyId);
}

function isVerseFamilyId(id: DockPanelId) {
  return (
    id === "outline" ||
    id === "history" ||
    id === "tester" ||
    id === "groupchat" ||
    id === "discordhub"
  );
}

function discordDockPlaceholder() {
  return (
    <div className="dock-panel-placeholder" style={{ padding: 12, opacity: 0.7 }}>
      Enable Discord plugin
    </div>
  );
}

function discordDockChildren(pluginContrib: PluginContributions) {
  const pluginUi = dockPanelPluginUi(pluginContrib, "groupchat");
  if (pluginUi) {
    return (
      <PluginWebviewPane tabId={pluginUiTabId(pluginUi.pluginId, pluginUi.uiPanelId)} />
    );
  }
  return discordDockPlaceholder();
}

function useVersePanelDefs(
  versePath: string | undefined,
  outlineEnabled: boolean,
  historyEnabled: boolean,
  testerEnabled: boolean,
  historyRefreshKey: number,
  pluginContrib: PluginContributions,
  onTearOffDiscord?: (at: { screenX: number; screenY: number }) => void,
) {
  const outline = useVerseOutlineDockPanel(versePath, outlineEnabled);
  const history = useVerseHistoryDockPanel(versePath, historyEnabled, historyRefreshKey);
  const tester = useTesterDockPanel(testerEnabled);

  return useMemo(
    () => ({
      outline: {
        title: "Outline",
        icon: <Icons.Outline />,
        actions: outline.actions,
        children: outline.children,
      },
      history: {
        title: "History",
        icon: <Icons.Clock />,
        actions: history.actions,
        children: history.children,
      },
      tester: {
        title: "Tester",
        icon: <Icons.Check />,
        busy: tester.busy,
        busyTitle: tester.busyTitle,
        actions: tester.actions,
        children: tester.children,
      },
      groupchat: {
        title: "Discord Ducky",
        icon: <Icons.Chat />,
        actions: (
          <button
            type="button"
            className="icon-btn"
            title="Open as tab"
            onClick={() => requestOpenDiscordTab()}
          >
            <Icons.Maximize />
          </button>
        ),
        onTearOffOutside: onTearOffDiscord,
        children: discordDockChildren(pluginContrib),
      },
      discordhub: {
        title: "Discord",
        icon: <Icons.Chat />,
        actions: undefined,
        children: discordDockPlaceholder(),
      },
    }),
    [
      outline.actions,
      outline.children,
      history.actions,
      history.children,
      tester.actions,
      tester.children,
      tester.busy,
      tester.busyTitle,
      onTearOffDiscord,
      pluginContrib,
    ],
  );
}

export const WorkspaceDockLayout = forwardRef<ChatSidebarHandle, WorkspaceDockLayoutProps>(
  function WorkspaceDockLayout(
    { layoutMode, children, activeFilePath, historyRefreshKey = 0, sidebarProps, variant = "default" },
    ref,
  ) {
    const dock = useWorkspaceDock();
    const leftSidebarRef = useRef<ChatSidebarHandle>(null);
    const rightSidebarRef = useRef<ChatSidebarHandle>(null);
    const [dragOverlay, setDragOverlayState] = useState<DockDropTarget>(null);
    const [isDockPanelDragging, setIsDockPanelDragging] = useState(false);

    const setDragOverlay = useCallback((target: DockDropTarget) => {
      setDragOverlayState((prev) => {
        if (prev === target) return prev;
        if (
          prev &&
          target &&
          prev.side === target.side &&
          prev.insert === target.insert
        ) {
          return prev;
        }
        return target;
      });
    }, []);

    useEffect(() => {
      if (variant === "focus") return;
      dock.setLeftRailOpen(layoutMode !== "sidebarHidden");
    }, [dock.setLeftRailOpen, layoutMode, variant]);

    useImperativeHandle(
      ref,
      () => ({
        createDucky: async () => {
          await (leftSidebarRef.current?.createDucky() ?? rightSidebarRef.current?.createDucky());
        },
        revealFileInSidebar: (path: string) => {
          leftSidebarRef.current?.revealFileInSidebar(path);
          rightSidebarRef.current?.revealFileInSidebar(path);
        },
        revealFileInExplorer: (path: string) => {
          leftSidebarRef.current?.revealFileInExplorer(path);
          rightSidebarRef.current?.revealFileInExplorer(path);
        },
      }),
      [],
    );

    const discordTabOpen = useDiscordTabOpen();
    const pluginContrib = usePluginContributions();
    const { prefs: discordUiPrefs } = useDiscordUiPrefs();
    const discordPluginOn = pluginContributesDockPanel(pluginContrib, "groupchat");
    const testerPluginOn = pluginContributesDockPanel(pluginContrib, "tester");
    const leftPanelIds = filterPanelsForVariant(
      dock.leftPanels,
      variant,
      discordTabOpen,
      discordPluginOn,
      discordUiPrefs.showInLeftSidebar,
      discordUiPrefs.showInRightSidebar,
      "left",
      testerPluginOn,
    );
    const rightPanelIds = filterPanelsForVariant(
      dock.rightPanels,
      variant,
      discordTabOpen,
      discordPluginOn,
      discordUiPrefs.showInLeftSidebar,
      discordUiPrefs.showInRightSidebar,
      "right",
      testerPluginOn,
    );

    const leftSidebarOnLeft = variant === "default" && hasSidebarPanels(leftPanelIds);
    const leftSidebarOnRight = variant === "default" && hasSidebarPanels(rightPanelIds);
    const verseOnLeft = hasVerseAuxPanels(leftPanelIds);
    const verseOnRight = hasVerseAuxPanels(rightPanelIds);

    const leftOpen =
      layoutMode !== "sidebarHidden" &&
      dock.leftRailOpen &&
      (leftSidebarOnLeft || verseOnLeft);
    const rightOpen = dock.rightRailOpen && (leftSidebarOnRight || verseOnRight);

    const versePath =
      activeFilePath && isVerseFile(activeFilePath) ? activeFilePath.replace(/\\/g, "/") : undefined;

    const leftStack = dock.stackForSide("left");
    const rightStack = dock.stackForSide("right");

    const { openFocusAtPoint } = useFocusWindow();
    const tearOffDiscord = useCallback(
      (at: { screenX: number; screenY: number }) => {
        // Hide dock immediately; focus window owns Discord while open.
        setDiscordTabOpen(true);
        void openFocusAtPoint(
          pluginUiTabId("discord", "discord-chat"),
          "Discord Ducky",
          at.screenX,
          at.screenY,
        );
      },
      [openFocusAtPoint],
    );

    const leftVersePanels = useVersePanelDefs(
      versePath,
      leftOpen && !leftStack.collapsed.outline,
      leftOpen && !leftStack.collapsed.history,
      testerPluginOn && leftOpen && !leftStack.collapsed.tester,
      historyRefreshKey,
      pluginContrib,
      tearOffDiscord,
    );
    const rightVersePanels = useVersePanelDefs(
      versePath,
      rightOpen && !rightStack.collapsed.outline,
      rightOpen && !rightStack.collapsed.history,
      testerPluginOn && rightOpen && !rightStack.collapsed.tester,
      historyRefreshKey,
      pluginContrib,
      tearOffDiscord,
    );

    const buildRailChildren = (
      versePanels: ReturnType<typeof useVersePanelDefs>,
      side: "left" | "right",
      ids: DockPanelId[],
      sidebarRef: React.RefObject<ChatSidebarHandle | null>,
    ) => (
      <DockRailFamilyStack
        side={side}
        panelIds={ids}
        versePanels={versePanels}
        sidebarProps={sidebarProps}
        sidebarRef={sidebarRef}
        onDockDropZoneChange={setDragOverlay}
        onDockDragChange={setIsDockPanelDragging}
      />
    );

    const leftVerseIds = leftPanelIds.filter(isVerseFamilyId);
    const rightVerseIds = rightPanelIds.filter(isVerseFamilyId);

    const leftMixed = useMemo(() => {
      if (!leftSidebarOnLeft && !verseOnLeft) return null;
      if (leftSidebarOnLeft && verseOnLeft) {
        return {
          panelIds: leftPanelIds,
          panels: undefined,
          children: buildRailChildren(leftVersePanels, "left", leftPanelIds, leftSidebarRef),
        };
      }
      if (leftSidebarOnLeft) {
        return {
          panelIds: leftPanelIds.filter((id) => id === "chats" || id === "files"),
          panels: undefined,
          children: buildRailChildren(leftVersePanels, "left", leftPanelIds, leftSidebarRef),
        };
      }
      return {
        panelIds: leftVerseIds,
        panels: undefined,
        children: buildRailChildren(leftVersePanels, "left", leftVerseIds, leftSidebarRef),
      };
    }, [leftPanelIds, leftSidebarOnLeft, leftVerseIds, leftVersePanels, sidebarProps, verseOnLeft]);

    const rightMixed = useMemo(() => {
      if (!leftSidebarOnRight && !verseOnRight) return null;
      if (leftSidebarOnRight && verseOnRight) {
        return {
          panelIds: rightPanelIds,
          panels: undefined,
          children: buildRailChildren(rightVersePanels, "right", rightPanelIds, rightSidebarRef),
        };
      }
      if (leftSidebarOnRight) {
        return {
          panelIds: rightPanelIds.filter((id) => id === "chats" || id === "files"),
          panels: undefined,
          children: buildRailChildren(rightVersePanels, "right", rightPanelIds, rightSidebarRef),
        };
      }
      return {
        panelIds: rightVerseIds,
        panels: undefined,
        children: buildRailChildren(rightVersePanels, "right", rightVerseIds, rightSidebarRef),
      };
    }, [leftSidebarOnRight, rightPanelIds, rightVerseIds, rightVersePanels, sidebarProps, verseOnRight]);

    const dragTargetSide = dockDropTargetSide(dragOverlay);
    const leftPeek = isDockPanelDragging && !leftMixed && dragTargetSide === "left";
    const rightPeek = isDockPanelDragging && !rightMixed && dragTargetSide === "right";

    return (
      <div className={`workspace-dock-layout workspace-dock-layout--${variant}`}>
        <div id="ducky-skin-left" className="ducky-skin-slot ducky-skin-slot--left" aria-hidden="true" />
        {leftMixed || leftPeek ? (
          <DockRail
            side="left"
            open={leftOpen || leftPeek}
            peek={leftPeek}
            panelIds={leftMixed?.panelIds}
            panels={leftMixed?.panels}
            dragOverlay={dragOverlay}
            onDockDropZoneChange={setDragOverlay}
            onDockDragChange={setIsDockPanelDragging}
          >
            {leftMixed?.children}
          </DockRail>
        ) : null}

        <div className="workspace-dock-center">{children}</div>

        {rightMixed || rightPeek ? (
          <DockRail
            side="right"
            open={rightOpen || rightPeek}
            peek={rightPeek}
            panelIds={rightMixed?.panelIds}
            panels={rightMixed?.panels}
            dragOverlay={dragOverlay}
            onDockDropZoneChange={setDragOverlay}
            onDockDragChange={setIsDockPanelDragging}
          >
            {rightMixed?.children}
          </DockRail>
        ) : null}
        <div id="ducky-skin-right" className="ducky-skin-slot ducky-skin-slot--right" aria-hidden="true" />
      </div>
    );
  },
);
