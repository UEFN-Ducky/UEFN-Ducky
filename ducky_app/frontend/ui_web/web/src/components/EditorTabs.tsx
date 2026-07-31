import { useEffect, useMemo, useRef, useState } from "react";

import { DuckyAvatar, DUCKY_AVATAR_SIZES } from "./ducky/DuckyAvatars";

import { Icons } from "../icons/Icons";

import { useVerseEditorOptional } from "../verse-editor/VerseEditorProvider";

import { FileTypeIcon } from "../verse-editor/components/FileTypeIcon";
import type { ChatTab, EditorTab } from "../types/panel";

import { ChatTabHoverCard, resolveEditorChatTab } from "./editor/ChatTabHoverCard";
import { FileTabHoverCard } from "./editor/FileTabHoverCard";
import { TerminalTabHoverCard } from "./editor/TerminalTabHoverCard";
import { EditorTabBarMenu } from "./editor/EditorTabBarMenu";
import { useTerminalBusyStatuses } from "../terminal/useTerminalBusyStatuses";

import { ContextMenu, useContextMenuState } from "./ContextMenu";
import {
  openTranslatedChat,
  requestChatTranslateWalk,
} from "../navigation/openTranslatedChat";
import { openVerseTranslatedTab } from "../navigation/openVerseTranslatedTab";
import {
  canVisualTranslateFile,
  isAutoTranslateChat,
  toggleAutoTranslateChat,
} from "../navigation/tabTranslatePrefs";
import { usePluginUiPrefs } from "../hooks/usePluginUiPrefs";
import { isEnglishLang } from "../views/settings/translationLanguages";
import {
  pluginContributesSettingsTab,
  usePluginContributions,
} from "../hooks/usePluginContributions";
import { contextMenuSeparator } from "../utils/sidebarContextMenuItems";

import { TruncatedText } from "./TruncatedText";

import { useTabScale } from "../hooks/useTabScale";
import { useEditorTabOverflowMode } from "../hooks/useEditorTabOverflowMode";

import { ScopedCss, useScopedClass } from "../utils/scopedCss";

import {
  endEditorTabDrag,
  getEditorTabDragData,
  getEditorTabGroupDragData,
  getLastDragScreenPoint,
  handleExternalTabDrop,
  isEditorTabDrag,
  markEditorTabDropped,
  setEditorTabDragData,
  trackEditorTabDragPointer,
  wasEditorTabDropped,
} from "../utils/editorTabDrag";

function onDocumentTabDragOver(e: DragEvent) {
  if (!isEditorTabDrag()) return;
  trackEditorTabDragPointer(e);
}

type TabDropHint = { tabId: string; before: boolean };

interface EditorTabsProps {
  groupId: string;
  tabs: EditorTab[];
  activeTabId: string | null;
  closeTab: (tabId: string) => void;
  onActivate: (tabId: string) => void;
  onFocusTab?: (tab: EditorTab, at?: { screenX: number; screenY: number }) => void;
  onSaveTab?: (tab: EditorTab) => void;
  onTabSelect?: (tabId: string) => void;
  onTabActivated?: (tab: EditorTab) => void;
  onPromoteTab?: (tabId: string) => void;
  onRevealFileInSidebar?: (path: string) => void;
  onRevealFileInExplorer?: (path: string) => void;
  onReorderTabs?: (
    draggedId: string,
    targetId: string,
    insertBefore: boolean,
    sourceGroupId: string,
  ) => void;
  completionAlertChatIds?: ReadonlySet<string>;
  onDismissChatAlert?: (chatId: string) => void;
  allChats?: ChatTab[];
  runningChatIds?: Set<string>;
  variant?: "default" | "focus";
  groupLocked?: boolean;
  onToggleGroupLock?: () => void;
}

export function EditorTabs({
  groupId,
  tabs,
  activeTabId,
  closeTab,
  onActivate,
  onFocusTab,
  onSaveTab,
  onTabSelect,
  onTabActivated,
  onPromoteTab,
  onRevealFileInSidebar,
  onRevealFileInExplorer,
  onReorderTabs,
  completionAlertChatIds,
  onDismissChatAlert,
  allChats = [],
  runningChatIds,
  variant = "default",
  groupLocked = false,
  onToggleGroupLock,
}: EditorTabsProps) {
  const isFocus = variant === "focus";

  const verseEditor = useVerseEditorOptional();
  const pluginContrib = usePluginContributions();

  const dirtyPaths = verseEditor?.dirtyPaths;
  const getFileDiagnosticSummary = verseEditor?.getFileDiagnosticSummary;

  const { menu, open, close } = useContextMenuState<EditorTab>();
  const { prefs: translationPrefs, setPref: setTranslationPref } = usePluginUiPrefs("translation");
  const uiLang =
    typeof translationPrefs.language === "string" && translationPrefs.language.trim()
      ? translationPrefs.language.trim()
      : "en";
  // Translation plugin on + non-English UI language → offer Translate on file/chat tabs.
  const translationReady =
    pluginContributesSettingsTab(pluginContrib, "Languages") && !isEnglishLang(uiLang);
  const canVisualTranslateTab =
    !!menu?.data.path &&
    menu.data.kind === "file" &&
    canVisualTranslateFile(menu.data.path) &&
    translationReady;
  const canChatTranslateTab =
    !!menu?.data.chatId && menu.data.kind === "chat" && translationReady;

  const tabsBarRef = useRef<HTMLDivElement>(null);
  const thumbDragRef = useRef<{ startX: number; startScrollLeft: number } | null>(null);
  const tabScale = useTabScale(tabsBarRef);
  const { mode: tabOverflowMode } = useEditorTabOverflowMode();
  const scaleScopeClass = useScopedClass("editor-tab-scale");
  const draggingTabRef = useRef<EditorTab | null>(null);
  const [draggingTabId, setDraggingTabId] = useState<string | null>(null);
  const [dropHint, setDropHint] = useState<TabDropHint | null>(null);
  const [tabScroll, setTabScroll] = useState({ scrollLeft: 0, scrollWidth: 0, clientWidth: 0 });

  const terminalSessionIds = useMemo(
    () =>
      tabs
        .filter((t) => t.kind === "terminal" && t.terminalSessionId)
        .map((t) => t.terminalSessionId!),
    [tabs],
  );
  const terminalBusyStatuses = useTerminalBusyStatuses(terminalSessionIds);

  const clearDragState = () => {
    draggingTabRef.current = null;
    setDraggingTabId(null);
    setDropHint(null);
    endEditorTabDrag();
  };

  const handleTabDragStart = (e: React.DragEvent, tab: EditorTab) => {
    if ((e.target as HTMLElement).closest(".editor-tab-actions, .editor-tab-close-btn")) {
      e.preventDefault();
      return;
    }
    draggingTabRef.current = tab;
    setDraggingTabId(tab.id);
    setEditorTabDragData(e.dataTransfer, tab, groupId);
    document.addEventListener("dragover", onDocumentTabDragOver);
    const el = e.currentTarget as HTMLElement;
    e.dataTransfer.setDragImage(el, Math.min(el.offsetWidth / 2, 80), el.offsetHeight / 2);
  };

  const handleTabDragEnd = (e: React.DragEvent) => {
    document.removeEventListener("dragover", onDocumentTabDragOver);
    trackEditorTabDragPointer(e.nativeEvent);
    const tab = draggingTabRef.current;
    const droppedInternally = wasEditorTabDropped();
    const screenPoint = getLastDragScreenPoint();
    clearDragState();
    if (!tab || !onFocusTab || isFocus || droppedInternally || groupLocked) return;
    // Once the drag leaves this window WebView2 hands it to the OS, so dragover stops
    // and nothing here can tell whether it was released over a focus window, the
    // desktop, or back inside this one. Always hand off: the host hit-tests the drop
    // point (falling back to the live cursor) and declines drops that landed here.
    onFocusTab(tab, screenPoint ?? { screenX: 0, screenY: 0 });
  };

  const updateDropHint = (e: React.DragEvent, tab: EditorTab) => {
    if (!isEditorTabDrag(e)) return;
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "move";
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const before = e.clientX < rect.left + rect.width / 2;
    setDropHint((prev) =>
      prev?.tabId === tab.id && prev.before === before ? prev : { tabId: tab.id, before },
    );
  };

  const handleTabDrop = (e: React.DragEvent, targetTab: EditorTab) => {
    if (!isEditorTabDrag(e) || !onReorderTabs) return;
    e.preventDefault();
    e.stopPropagation();
    if (handleExternalTabDrop(e)) {
      clearDragState();
      return;
    }
    markEditorTabDropped();
    const draggedId = getEditorTabDragData(e.dataTransfer);
    const sourceGroupId = getEditorTabGroupDragData(e.dataTransfer);
    if (!draggedId || !sourceGroupId) {
      clearDragState();
      return;
    }
    const insertBefore = dropHint?.tabId === targetTab.id ? dropHint.before : true;
    onReorderTabs(draggedId, targetTab.id, insertBefore, sourceGroupId);
    clearDragState();
  };

  const handleBarDragOver = (e: React.DragEvent) => {
    if (!isEditorTabDrag(e)) return;
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "move";
    const lastTab = tabs[tabs.length - 1];
    if (!lastTab) return;
    setDropHint({ tabId: lastTab.id, before: false });
  };

  const handleBarDrop = (e: React.DragEvent) => {
    if (!isEditorTabDrag(e) || !onReorderTabs) return;
    e.preventDefault();
    e.stopPropagation();
    if (handleExternalTabDrop(e)) {
      clearDragState();
      return;
    }
    markEditorTabDropped();
    const draggedId = getEditorTabDragData(e.dataTransfer);
    const sourceGroupId = getEditorTabGroupDragData(e.dataTransfer);
    const lastTab = tabs[tabs.length - 1];
    if (!draggedId || !sourceGroupId || !lastTab) {
      clearDragState();
      return;
    }
    onReorderTabs(draggedId, lastTab.id, false, sourceGroupId);
    clearDragState();
  };

  const handleTabClick = (tab: EditorTab) => {
    if (tab.kind === "chat" && tab.chatId) {
      onDismissChatAlert?.(tab.chatId);
    }
    onTabSelect?.(tab.id);
    onActivate(tab.id);
    window.requestAnimationFrame(() => onTabActivated?.(tab));
  };

  const isDirty = (tab: EditorTab) =>
    tab.kind === "file" &&
    !!tab.path &&
    !!dirtyPaths?.has(tab.path.replace(/\\/g, "/"));

  const hasDirtyTabs = tabs.some(isDirty);

  const handleSaveAll = () => {
    for (const tab of tabs) {
      if (isDirty(tab)) onSaveTab?.(tab);
    }
  };

  const handleCloseAll = () => {
    for (const tab of tabs) closeTab(tab.id);
  };

  const handleCloseSaved = () => {
    for (const tab of tabs) {
      if (!isDirty(tab)) closeTab(tab.id);
    }
  };

  useEffect(() => {
    return () => document.removeEventListener("dragover", onDocumentTabDragOver);
  }, []);

  useEffect(() => {
    if (!activeTabId) return;
    const activeEl = tabsBarRef.current?.querySelector<HTMLElement>(".editor-tab.is-active");
    activeEl?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [activeTabId, tabs]);

  // Chrome-style: wheel scrolls the tab strip when tabs overflow.
  useEffect(() => {
    const el = tabsBarRef.current;
    if (!el) return;

    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey) return;
      if (el.scrollWidth <= el.clientWidth + 1) return;
      e.preventDefault();
      el.scrollLeft += Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    };

    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  useEffect(() => {
    const el = tabsBarRef.current;
    if (!el || tabOverflowMode !== "scrollbar") return;

    const update = () => {
      setTabScroll({
        scrollLeft: el.scrollLeft,
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
      });
    };

    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, [tabOverflowMode, tabs]);

  const tabStripOverflows = tabScroll.scrollWidth > tabScroll.clientWidth + 1;
  const tabThumbWidth = tabStripOverflows
    ? Math.max(24, (tabScroll.clientWidth / tabScroll.scrollWidth) * tabScroll.clientWidth)
    : 0;
  const tabThumbMaxLeft = Math.max(0, tabScroll.clientWidth - tabThumbWidth);
  const tabThumbLeft =
    tabScroll.scrollWidth <= tabScroll.clientWidth
      ? 0
      : (tabScroll.scrollLeft / (tabScroll.scrollWidth - tabScroll.clientWidth)) * tabThumbMaxLeft;

  const handleTabThumbPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const el = tabsBarRef.current;
    if (!el) return;
    thumbDragRef.current = { startX: e.clientX, startScrollLeft: el.scrollLeft };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const handleTabThumbPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = thumbDragRef.current;
    const el = tabsBarRef.current;
    if (!drag || !el) return;
    const scrollable = el.scrollWidth - el.clientWidth;
    const track = el.clientWidth;
    const thumb = Math.max(24, (el.clientWidth / el.scrollWidth) * el.clientWidth);
    const scrollRatio = scrollable / Math.max(1, track - thumb);
    el.scrollLeft = drag.startScrollLeft + (e.clientX - drag.startX) * scrollRatio;
  };

  const handleTabThumbPointerUp = () => {
    thumbDragRef.current = null;
  };

  if (tabs.length === 0) return null;

  return (
    <>
      <ScopedCss selector={`.${scaleScopeClass}`} rules={{ "--tab-scale": String(tabScale) }} />
      <div className={`${isFocus ? "" : "no-drag "}editor-tabs-shell${isFocus ? " editor-tabs-shell--focus" : ""} ${scaleScopeClass}`}>
        <div className="editor-tabs-scroll-wrap">
          <div
            ref={tabsBarRef}
            className={`${isFocus ? "" : "no-drag "}editor-tabs-bar${isFocus ? " editor-tabs-bar--focus" : ""} editor-tabs-bar--${tabOverflowMode}`}
            onDragOver={handleBarDragOver}
            onDrop={handleBarDrop}
            onDragLeave={(e) => {
              if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) {
                setDropHint(null);
              }
            }}
          >
          {tabs.map((tab) => {
            const isActive = tab.id === activeTabId;
            const isDragging = draggingTabId === tab.id;
            const showBefore = dropHint?.tabId === tab.id && dropHint.before;
            const showAfter = dropHint?.tabId === tab.id && !dropHint.before;
            const tabDirty = isDirty(tab);
            const diagnosticSummary = tab.path ? getFileDiagnosticSummary?.(tab.path) : undefined;
            const diagnosticErrors = diagnosticSummary?.errors ?? 0;
            const diagnosticWarnings = diagnosticSummary?.warnings ?? 0;

            const tabIcon = (
              <div
                className={`editor-tab-icon${
                  tab.kind === "chat" &&
                  tab.chatId &&
                  completionAlertChatIds?.has(tab.chatId)
                    ? " chat-completion-alert"
                    : ""
                }`}
              >
                {tab.kind === "file" ? (
                  <FileTypeIcon
                    path={tab.path ?? ""}
                    size={16}
                    diagnosticErrors={diagnosticErrors}
                    diagnosticWarnings={diagnosticWarnings}
                  />
                ) : tab.kind === "terminal" ? (
                  <Icons.Terminal />
                ) : tab.kind === "plan" ? (
                  <Icons.Plan />
                ) : tab.kind === "usage" ? (
                  <Icons.Chart />
                ) : tab.kind === "settings" ? (
                  <Icons.Settings />
                ) : tab.kind === "verse-translated" ? (
                  <Icons.Globe />
                ) : (
                  <DuckyAvatar styleId={tab.duckyStyle} size={DUCKY_AVATAR_SIZES.tab} />
                )}
              </div>
            );

            const tabLabel = (
              <TruncatedText className="ui-flex-1-min">
                {tab.name}
                {tabDirty ? <span className="editor-tab-dirty">●</span> : null}
              </TruncatedText>
            );

            const tabBody = (
              <div className="editor-tab-hover-body">
                {tabIcon}
                {tabLabel}
              </div>
            );

            const hoverDisabled = !!draggingTabId;
            let tabHoverBody = tabBody;
            if (tab.kind === "file" && tab.path) {
              tabHoverBody = (
                <FileTabHoverCard
                  tab={tab}
                  isDirty={tabDirty}
                  diagnosticErrors={diagnosticErrors}
                  diagnosticWarnings={diagnosticWarnings}
                  disabled={hoverDisabled}
                >
                  {tabBody}
                </FileTabHoverCard>
              );
            } else if (tab.kind === "chat" && tab.chatId) {
              tabHoverBody = (
                <ChatTabHoverCard
                  chat={resolveEditorChatTab(tab.chatId, tab, allChats)}
                  isRunning={runningChatIds?.has(tab.chatId) ?? false}
                  hasCompletionAlert={completionAlertChatIds?.has(tab.chatId) ?? false}
                  disabled={hoverDisabled}
                >
                  {tabBody}
                </ChatTabHoverCard>
              );
            } else if (tab.kind === "terminal") {
              tabHoverBody = (
                <TerminalTabHoverCard
                  tab={tab}
                  busyStatus={
                    tab.terminalSessionId
                      ? terminalBusyStatuses.get(tab.terminalSessionId) ?? null
                      : null
                  }
                  disabled={hoverDisabled}
                >
                  {tabBody}
                </TerminalTabHoverCard>
              );
            }

            return (
              <div
                key={tab.id}
                data-tab-id={tab.id}
                draggable
                className={`no-drag editor-tab${isActive ? " is-active" : ""}${isDragging ? " is-dragging" : ""}${showBefore ? " is-drop-before" : ""}${showAfter ? " is-drop-after" : ""}${tab.preview ? " is-preview" : ""}`}
                onDragStart={(e) => handleTabDragStart(e, tab)}
                onDragEnd={handleTabDragEnd}
                onDragOver={(e) => updateDropHint(e, tab)}
                onDrop={(e) => handleTabDrop(e, tab)}
                onClick={() => handleTabClick(tab)}
                onDoubleClick={() => onPromoteTab?.(tab.id)}
                onContextMenu={(e) => open(e, tab)}
              >
                {tabHoverBody}

                <div className="editor-tab-actions">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      closeTab(tab.id);
                    }}
                    className="icon-btn no-drag editor-tab-btn editor-tab-close-btn"
                    title="Close"
                    aria-label="Close tab"
                  >
                    <Icons.Close />
                  </button>
                </div>
              </div>
            );
          })}
          </div>
          {tabOverflowMode === "scrollbar" && tabStripOverflows ? (
            <div
              className="editor-tabs-scrollbar-overlay is-scrollable"
              aria-hidden="true"
            >
              <div
                className="editor-tabs-scrollbar-thumb"
                style={{ width: tabThumbWidth, transform: `translateX(${tabThumbLeft}px)` }}
                onPointerDown={handleTabThumbPointerDown}
                onPointerMove={handleTabThumbPointerMove}
                onPointerUp={handleTabThumbPointerUp}
                onPointerCancel={handleTabThumbPointerUp}
              />
            </div>
          ) : null}
        </div>
        <div className="editor-tab-bar-actions">
          <EditorTabBarMenu
            hasDirtyTabs={hasDirtyTabs}
            groupLocked={groupLocked}
            onSaveAll={onSaveTab ? handleSaveAll : undefined}
            onCloseAll={handleCloseAll}
            onCloseSaved={handleCloseSaved}
            onToggleGroupLock={onToggleGroupLock}
          />
        </div>
      </div>

      {menu ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          onClose={close}
          items={[
            {
              id: "save",
              label: "Save",
              disabled: !isDirty(menu.data),
              onClick: () => onSaveTab?.(menu.data),
            },
            ...(canVisualTranslateTab
              ? [
                  {
                    id: "translate",
                    label: "Translate",
                    onClick: () => openVerseTranslatedTab(menu.data.path!),
                  },
                  contextMenuSeparator("sep-translate"),
                ]
              : []),
            ...(canChatTranslateTab
              ? [
                  {
                    id: "translate",
                    label: "Translate",
                    onClick: () => {
                      const chat = resolveEditorChatTab(menu.data.chatId!, menu.data, allChats);
                      if (!isAutoTranslateChat(chat.id, translationPrefs)) {
                        toggleAutoTranslateChat(chat.id, translationPrefs, setTranslationPref);
                      }
                      openTranslatedChat(chat);
                      window.setTimeout(() => requestChatTranslateWalk(), 50);
                    },
                  },
                  contextMenuSeparator("sep-translate"),
                ]
              : []),
            ...(menu.data.kind === "file" && menu.data.path && onRevealFileInSidebar && onRevealFileInExplorer
              ? [
                  {
                    id: "reveal-sidebar",
                    label: "Reveal in Explorer",
                    onClick: () => onRevealFileInSidebar(menu.data.path!.replace(/\\/g, "/")),
                  },
                  {
                    id: "reveal-os",
                    label: "Reveal in File Explorer",
                    onClick: () => onRevealFileInExplorer(menu.data.path!.replace(/\\/g, "/")),
                  },
                ]
              : []),
            ...(isFocus || !onFocusTab || groupLocked
              ? []
              : [
                  {
                    id: "focus",
                    label: "Focus",
                    onClick: () => onFocusTab(menu.data),
                  },
                ]),
            {
              id: "close",
              label: "Close",
              onClick: () => closeTab(menu.data.id),
            },
          ]}
        />
      ) : null}
    </>
  );
}
