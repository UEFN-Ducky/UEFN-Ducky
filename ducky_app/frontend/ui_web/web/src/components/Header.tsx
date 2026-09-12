import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Icons } from "../icons/Icons";
import { ProjectSelector } from "./ProjectSelector";
import { ConnectionStatusDropdown } from "./ConnectionStatusDropdown";
import { VerseProblemsDropdown } from "./VerseProblemsDropdown";
import { TerminalHeaderDropdown } from "../terminal/TerminalHeaderDropdown";
import { LanguageHeaderDropdown } from "./LanguageHeaderDropdown";
import { PluginSurfaceBoundary } from "../plugin-ui/PluginSurfaceBoundary";
import { useAppHeaderActions, useProblemsMenuOpen } from "../contexts/AppHeaderActionsContext";
import { useQuickOpenBridge } from "../contexts/QuickOpenBridge";
import { useNavigationHistoryOptional } from "../navigation/NavigationHistoryContext";
import { useRightRailOpen } from "../hooks/useRightRailOpen";
import { useAppearance } from "../theme/AppearanceContext";
import { QuickOpenBar } from "./quick-open/QuickOpenBar";
import type { ChatLayoutMode, ListenerStatus, ProjectInfo, ViewId } from "../types/panel";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { isNativeWindowChrome } from "../utils/nativeWindowChrome";
import { requestOpenSettings } from "../navigation/openSettingsTab";
import { requestOpenChangesTab } from "../navigation/openChangesTab";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { useDiscordUiPrefs } from "../hooks/usePluginUiPrefs";
import { useStoreUpdateBadge } from "../hooks/useStoreUpdateBadge";
import {
  resolvePluginHeaderAction,
  resolvePluginHeaderIcon,
  sortPluginHeaderButtons,
} from "../hooks/pluginHeaderActions";
import { useUiTarget } from "../ui-targets/registry";
import { DropdownPanel } from "./DropdownPanel";
import { RemoteViewControls, RemoteWindowSelect } from "./RemoteWindowView";
import type { PluginHeaderButton } from "../hooks/usePluginContributions";

interface HeaderProps {
  variant?: "main" | "focus";
  currentView?: ViewId;
  setView?: (v: ViewId) => void;
  isOnline: boolean;
  isWedged?: boolean;
  statusText?: string;
  listenerStatus?: ListenerStatus;
  projectMatch?: boolean;
  uefnProjectName?: string;
  project?: ProjectInfo;
  layoutMode: ChatLayoutMode;
  cycleLayoutMode: () => void;
  hasProject: boolean;
  onProjectChanged?: () => void;
  onCloseWindow?: () => void;
  onProblemsOpenChange?: (open: boolean) => void;
  showQuickOpen?: boolean;
  watchWindowId?: string;
  onWatchWindowId?: (id: string) => void;
}

const LAYOUT_TOGGLE_META: Record<ChatLayoutMode, { title: string; Icon: () => JSX.Element }> = {
  full: { title: "Hide sidebar", Icon: Icons.PanelLeft },
  sidebarHidden: { title: "Show sidebar", Icon: Icons.PanelLeftClose },
};

const RIGHT_RAIL_TOGGLE_META = {
  open: { title: "Hide right sidebar", Icon: Icons.PanelRight },
  closed: { title: "Show right sidebar", Icon: Icons.PanelRightClose },
} as const;

const COMPACT_HEADER_MQ = "(max-width: 720px)";

function useCompactHeader() {
  return useSyncExternalStore(
    (onChange) => {
      const mq = window.matchMedia(COMPACT_HEADER_MQ);
      mq.addEventListener("change", onChange);
      return () => mq.removeEventListener("change", onChange);
    },
    () => window.matchMedia(COMPACT_HEADER_MQ).matches,
    () => true,
  );
}

function PluginHeaderItem({
  btn,
  layout,
  onPicked,
}: {
  btn: PluginHeaderButton;
  layout: "icon" | "row";
  onPicked?: () => void;
}) {
  const pluginId = (btn.plugin_id || btn.id || "plugin").trim().toLowerCase();
  const key = `${pluginId}:${btn.id}`;
  const title = btn.title || btn.id;
  const isTranslation = btn.plugin_id === "translation" || btn.id === "translation";
  if (isTranslation) {
    return (
      <PluginSurfaceBoundary key={key} pluginId={pluginId || "translation"} surface="header-button" compact>
        <LanguageHeaderDropdown
          icon={resolvePluginHeaderIcon(btn.icon)}
          title={title}
          layout={layout}
        />
      </PluginSurfaceBoundary>
    );
  }
  const onClick = resolvePluginHeaderAction(btn.action, btn.plugin_id);
  if (!onClick) return null;
  if (layout === "row") {
    return (
      <PluginSurfaceBoundary key={key} pluginId={pluginId} surface="header-button" compact>
        <button
          type="button"
          className="plugin-header-menu-item"
          onClick={() => {
            onClick();
            onPicked?.();
          }}
        >
          {resolvePluginHeaderIcon(btn.icon)}
          <span className="plugin-header-menu-item-label">{title}</span>
        </button>
      </PluginSurfaceBoundary>
    );
  }
  return (
    <PluginSurfaceBoundary key={key} pluginId={pluginId} surface="header-button" compact>
      <button
        type="button"
        className="icon-btn no-drag plugin-header-btn"
        title={title}
        aria-label={title}
        onClick={onClick}
      >
        {resolvePluginHeaderIcon(btn.icon)}
      </button>
    </PluginSurfaceBoundary>
  );
}

function PluginHeaderMenu({ buttons }: { buttons: PluginHeaderButton[] }) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  if (!buttons.length) return null;
  return (
    <div className="choice-dropdown choice-dropdown--compact plugin-header-menu no-drag">
      <button
        ref={anchorRef}
        type="button"
        className={`choice-dropdown-trigger${open ? " is-open" : ""}`}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label="Plugins"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="choice-dropdown-trigger-copy">
          <span className="choice-dropdown-trigger-label">Plugins</span>
        </span>
        <span className={`choice-dropdown-chevron${open ? " is-open" : ""}`} aria-hidden>
          <Icons.ChevronDown />
        </span>
      </button>
      <DropdownPanel open={open} anchorRef={anchorRef} onClose={() => setOpen(false)} minWidth={220}>
        <div className="plugin-header-menu-list" role="menu">
          {buttons.map((btn) => (
            <PluginHeaderItem
              key={`${btn.plugin_id || btn.id}:${btn.id}`}
              btn={btn}
              layout="row"
              onPicked={() => setOpen(false)}
            />
          ))}
        </div>
      </DropdownPanel>
    </div>
  );
}

function HeaderToolsMenu({
  canBack,
  canForward,
  onBack,
  onForward,
  showNav,
  layoutTitle,
  onCycleLayout,
  sidebarEnabled,
  rightTitle,
  onToggleRight,
  rightEnabled,
  showWorkflow,
  onCompile,
  onPush,
  canPush,
  compileBusy,
  onSearch,
  onProblems,
  onTerminal,
  onLedger,
}: {
  canBack: boolean;
  canForward: boolean;
  onBack: () => void;
  onForward: () => void;
  showNav: boolean;
  layoutTitle: string;
  onCycleLayout: () => void;
  sidebarEnabled: boolean;
  rightTitle: string;
  onToggleRight: () => void;
  rightEnabled: boolean;
  showWorkflow: boolean;
  onCompile?: () => void;
  onPush?: () => void;
  canPush?: boolean;
  compileBusy?: boolean;
  onSearch?: () => void;
  onProblems?: () => void;
  onTerminal?: () => void;
  onLedger?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  const item = (label: string, onClick?: () => void, disabled?: boolean) => (
    <button
      type="button"
      className="plugin-header-menu-item"
      disabled={disabled || !onClick}
      onClick={() => {
        onClick?.();
        setOpen(false);
      }}
    >
      <span className="plugin-header-menu-item-label">{label}</span>
    </button>
  );
  return (
    <div className="choice-dropdown choice-dropdown--compact header-tools-menu no-drag">
      <button
        ref={anchorRef}
        type="button"
        className={`choice-dropdown-trigger${open ? " is-open" : ""}`}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label="Tools"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="choice-dropdown-trigger-copy">
          <span className="choice-dropdown-trigger-label">Tools</span>
        </span>
        <span className={`choice-dropdown-chevron${open ? " is-open" : ""}`} aria-hidden>
          <Icons.ChevronDown />
        </span>
      </button>
      <DropdownPanel open={open} anchorRef={anchorRef} onClose={() => setOpen(false)} minWidth={200}>
        <div className="plugin-header-menu-list" role="menu">
          {showNav ? item("Back", onBack, !canBack) : null}
          {showNav ? item("Forward", onForward, !canForward) : null}
          {item(layoutTitle, sidebarEnabled ? onCycleLayout : undefined, !sidebarEnabled)}
          {item(rightTitle, rightEnabled ? onToggleRight : undefined, !rightEnabled)}
          {item("Search", onSearch)}
          {showWorkflow ? item("Build Verse", onCompile, compileBusy) : null}
          {showWorkflow && canPush ? item("Push Verse", onPush, compileBusy) : null}
          {item("Problems", onProblems)}
          {item("Terminal", onTerminal)}
          {item("Ledger", onLedger)}
        </div>
      </DropdownPanel>
    </div>
  );
}

export function Header({
  variant = "main",
  currentView = "chat",
  setView,
  isOnline,
  isWedged = false,
  statusText,
  listenerStatus,
  projectMatch,
  uefnProjectName,
  project = { name: "", path: "", slug: "" },
  layoutMode,
  cycleLayoutMode,
  hasProject,
  onProjectChanged,
  onCloseWindow,
  onProblemsOpenChange,
  showQuickOpen = false,
  watchWindowId = "",
  onWatchWindowId,
}: HeaderProps) {
  const [isMaximized, setIsMaximized] = useState(false);
  const isFocus = variant === "focus";
  // Full-page settings overlay only when no project (welcome). With a project, Settings is an editor tab.
  const isSettingsOverlay = !isFocus && !hasProject && currentView === "settings";
  const { guardUnsavedChanges } = useAppearance();

  const handleSettingsToggle = async () => {
    if (hasProject) {
      requestOpenSettings();
      return;
    }
    if (!setView) return;
    if (isSettingsOverlay) {
      if (await guardUnsavedChanges()) setView("chat");
      return;
    }
    setView("settings");
  };

  const handleClose = () => {
    if (onCloseWindow) {
      onCloseWindow();
      return;
    }
    const api = getApi();
    if (api) void api.hide_window();
  };

  const handleMinimize = () => {
    const api = getApi();
    if (api) void api.minimize_window();
  };

  const handleMaximize = async () => {
    const api = getApi();
    if (!api) return;
    const next = await api.toggle_maximize();
    setIsMaximized(next);
  };

  // Native chrome publishes this window's OS state. Observe it locally instead of
  // spawning API workers and JS replies throughout every resize gesture.
  // Other platforms retain the resize-based fallback.
  useEffect(() => {
    let cancelled = false;
    let throttle: ReturnType<typeof setTimeout> | null = null;

    const sync = () => {
      if (isRemote()) return;
      if (isNativeWindowChrome()) {
        setIsMaximized(document.documentElement.classList.contains("window-maximized"));
        return;
      }
      const api = getApi();
      if (!api?.is_window_maximized) return;
      void api.is_window_maximized()
        .then((max) => {
          if (!cancelled && !isNativeWindowChrome()) setIsMaximized(!!max);
        })
        .catch(() => {});
    };

    const onResize = () => {
      if (isNativeWindowChrome()) return;
      if (throttle) return; // coalesce a resize-drag burst
      sync(); // leading edge → instant flip on double-click maximise/restore
      throttle = setTimeout(() => {
        throttle = null;
        sync(); // trailing edge → settle to the final state
      }, 150);
    };

    const observer = new MutationObserver(() => {
      if (isNativeWindowChrome()) sync();
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    sync(); // initial: window may already be maximised (e.g. restored session)
    window.addEventListener("pywebviewready", sync);
    window.addEventListener("resize", onResize);
    return () => {
      cancelled = true;
      if (throttle) clearTimeout(throttle);
      observer.disconnect();
      window.removeEventListener("pywebviewready", sync);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  const connectionStatus: ListenerStatus = listenerStatus ?? {
    online: isOnline,
    wedged: isWedged,
    version: "",
    status_text: statusText,
    project_match: projectMatch,
    uefn_project_name: uefnProjectName,
  };
  const nav = useNavigationHistoryOptional();
  const showNav = !isFocus && !!nav;
  const sidebarEnabled = (isFocus || !isSettingsOverlay) && hasProject;
  const headerActions = useAppHeaderActions();
  const { rightRailOpen, hasRightPanels, toggleRightRail } = useRightRailOpen();
  const pluginContrib = usePluginContributions();
  const { prefs: discordUiPrefs } = useDiscordUiPrefs();
  const { hasUpdates: hasStoreUpdates } = useStoreUpdateBadge();
  const narrowHeader = useCompactHeader();
  const pluginHeaderButtons = useMemo(() => {
    if (!hasProject || isFocus || isSettingsOverlay) return [];
    return sortPluginHeaderButtons(pluginContrib.header_buttons).filter((btn) => {
      const isTranslation = btn.plugin_id === "translation" || btn.id === "translation";
      if (!isTranslation && !resolvePluginHeaderAction(btn.action, btn.plugin_id)) return false;
      // Discord placement prefs gate the Discord button; other plugins always show.
      if (btn.plugin_id === "discord" || btn.id === "discord") {
        return discordUiPrefs.showInHeader;
      }
      return true;
    });
  }, [
    discordUiPrefs.showInHeader,
    hasProject,
    isFocus,
    isSettingsOverlay,
    pluginContrib.header_buttons,
  ]);
  const compactHeader = narrowHeader;
  const { setProblemsMenuOpen } = useProblemsMenuOpen();
  const { openPalette } = useQuickOpenBridge();
  const pluginHeader = compactHeader ? (
    <PluginHeaderMenu buttons={pluginHeaderButtons} />
  ) : (
    pluginHeaderButtons.map((btn) => (
      <PluginHeaderItem key={`${btn.plugin_id || btn.id}:${btn.id}`} btn={btn} layout="icon" />
    ))
  );
  const showEditorActions = isFocus || !isSettingsOverlay;
  const saveAction = showEditorActions ? headerActions.save : null;
  const workflowAction = showEditorActions && hasProject ? headerActions.verseWorkflow : null;
  const problemsAction = showEditorActions && hasProject ? headerActions.problems : null;
  const terminalAction = showEditorActions && hasProject ? headerActions.terminal : null;
  // The ledger is always reachable once a project is open, even with no other editor action.
  const showChanges = showEditorActions && hasProject;
  const layoutToggle = LAYOUT_TOGGLE_META[layoutMode];
  const LayoutToggleIcon = layoutToggle.Icon;
  const rightRailToggle = RIGHT_RAIL_TOGGLE_META[rightRailOpen ? "open" : "closed"];
  const RightRailToggleIcon = rightRailToggle.Icon;
  const rightSidebarEnabled = sidebarEnabled && hasRightPanels;

  const navButtons = showNav ? (
    <div className="app-header-nav no-drag">
      <button
        type="button"
        className="icon-btn app-header-nav-btn"
        title="Back (Alt+Left)"
        aria-label="Go back"
        onClick={() => nav?.back()}
        disabled={!nav?.canBack}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M15 18l-6-6 6-6" />
        </svg>
      </button>
      <button
        type="button"
        className="icon-btn app-header-nav-btn"
        title="Forward (Alt+Right)"
        aria-label="Go forward"
        onClick={() => nav?.forward()}
        disabled={!nav?.canForward}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M9 18l6-6-6-6" />
        </svg>
      </button>
    </div>
  ) : null;

  const showWorkflow = !!(workflowAction?.connected && isOnline);
  const headerTargetRef = useUiTarget("shell.header", { kind: "button", label: "Top bar", route: "chat" });
  const settingsTargetRef = useUiTarget("header.settings", {
    kind: "button",
    label: "Settings",
    route: "settings",
  });
  const changesTargetRef = useUiTarget("header.changes", {
    kind: "button",
    label: "Ledger",
    route: "changes",
  });

  return (
    <header
      ref={headerTargetRef}
      className={`glass-panel app-header${isFocus ? " app-header--focus" : ""}${isSettingsOverlay ? " app-header--settings" : " drag-region app-drag-surface"}${compactHeader ? " app-header--compact" : ""}${isRemote() ? " app-header--remote" : ""}`}
    >
      <div id="ducky-skin-header" className="ducky-skin-slot ducky-skin-slot--header" aria-hidden="true" />
      <div className={`app-header-left${isSettingsOverlay ? " app-header-left--settings" : ""}`}>
        {!isSettingsOverlay ? (
          <>
          <div className="app-header-status-row">
            <span ref={settingsTargetRef}>
              <ConnectionStatusDropdown
                status={connectionStatus}
                projectName={project.path?.trim() ? project.name : undefined}
                hasStoreUpdates={hasStoreUpdates}
                onOpenSettings={() => void handleSettingsToggle()}
              />
            </span>
            {isFocus ? (
              project.path?.trim() ? (
                <span className="app-header-project-label" title={project.path}>
                  {project.name}
                </span>
              ) : null
            ) : (
              <ProjectSelector
                project={project}
                onProjectChanged={onProjectChanged}
                uefnProjectName={uefnProjectName}
                projectMatch={projectMatch}
                listenerOnline={isOnline}
              />
            )}
          </div>
          </>
        ) : (
          <button
            type="button"
            className="no-drag connection-status-btn is-active"
            title="Back"
            onClick={() => void handleSettingsToggle()}
          >
            <span className="connection-status-back">&lt; Back</span>
          </button>
        )}
      </div>

      {isSettingsOverlay || isFocus ? (
        <div className="app-header-drag-fill drag-region app-drag-surface" aria-hidden="true" />
      ) : null}

      {!isSettingsOverlay && !isFocus ? (
        <div className="app-header-center drag-region app-drag-surface">
          {compactHeader ? (
            <HeaderToolsMenu
              showNav={showNav}
              canBack={!!nav?.canBack}
              canForward={!!nav?.canForward}
              onBack={() => nav?.back()}
              onForward={() => nav?.forward()}
              layoutTitle={layoutToggle.title}
              onCycleLayout={cycleLayoutMode}
              sidebarEnabled={sidebarEnabled}
              rightTitle={rightRailToggle.title}
              onToggleRight={toggleRightRail}
              rightEnabled={rightSidebarEnabled}
              showWorkflow={showWorkflow}
              onCompile={workflowAction?.onCompile}
              onPush={workflowAction?.onPush}
              canPush={workflowAction?.canPush}
              compileBusy={workflowAction?.busy || workflowAction?.buildState === 3}
              onSearch={showQuickOpen ? () => openPalette("file") : undefined}
              onProblems={problemsAction ? () => setProblemsMenuOpen(true) : undefined}
              onTerminal={
                terminalAction
                  ? () => {
                      const active = terminalAction.terminals.find((t) => t.active);
                      if (active) terminalAction.onGotoTerminal(active.id);
                      else terminalAction.onNewTerminal();
                    }
                  : undefined
              }
              onLedger={showChanges ? () => requestOpenChangesTab() : undefined}
            />
          ) : (
            <>
              {navButtons}
              <button
                type="button"
                onClick={cycleLayoutMode}
                className={`icon-btn no-drag sidebar-toggle-btn sidebar-toggle-btn--${layoutMode} ${sidebarEnabled ? "" : "is-disabled"}`}
                title={layoutToggle.title}
              >
                <LayoutToggleIcon />
              </button>
            </>
          )}
          {showQuickOpen && !compactHeader ? <QuickOpenBar /> : null}
          {compactHeader ? null : (
            <button
              type="button"
              onClick={toggleRightRail}
              className={`icon-btn no-drag sidebar-toggle-btn sidebar-toggle-btn--right sidebar-toggle-btn--${rightRailOpen ? "full" : "sidebarHidden"} ${rightSidebarEnabled ? "" : "is-disabled"}`}
              title={rightRailToggle.title}
            >
              <RightRailToggleIcon />
            </button>
          )}
          {pluginHeader}
        </div>
      ) : showQuickOpen ? (
        <div className="app-header-center drag-region app-drag-surface">
          <QuickOpenBar />
        </div>
      ) : null}

      <div className={`app-header-trailing${isSettingsOverlay ? " app-header-trailing--settings" : ""}`}>
        {showWorkflow || terminalAction || problemsAction || showChanges ? (
          <span className="app-header-editor-actions">
            {showWorkflow && workflowAction ? (
              <div
                className="app-header-verse-workflow no-drag"
                title={workflowAction.lastLog || "Verse workflow"}
              >
                <button
                  type="button"
                  className="connection-status-btn app-header-workflow-btn"
                  onClick={workflowAction.onCompile}
                  disabled={workflowAction.busy || workflowAction.buildState === 3}
                  title="Build Verse project"
                >
                  <img src={workflowAction.buildIconSrc} alt="" draggable={false} />
                </button>
                {workflowAction.canPush ? (
                  <button
                    type="button"
                    className="connection-status-btn app-header-workflow-btn"
                    onClick={workflowAction.onPush}
                    disabled={workflowAction.busy}
                    title="Push Verse changes"
                  >
                    <img src="/verse-workflow/verse-icon-upload.svg" alt="" draggable={false} />
                  </button>
                ) : null}
              </div>
            ) : null}
            {problemsAction ? (
              <span className="app-header-problems">
                <VerseProblemsDropdown {...problemsAction} onOpenChange={onProblemsOpenChange} />
              </span>
            ) : null}
            {terminalAction ? (
              <span className="app-header-terminal">
                <TerminalHeaderDropdown {...terminalAction} />
              </span>
            ) : null}
            {showChanges ? (
              <button
                ref={changesTargetRef}
                type="button"
                className="icon-btn app-header-changes-btn"
                title="Ledger — everything the AI changed, and how to undo it"
                aria-label="Open ledger"
                onClick={() => requestOpenChangesTab()}
              >
                <Icons.Clock />
              </button>
            ) : null}
          </span>
        ) : null}
        {saveAction ? (
          <button
            type="button"
            className="app-header-save-btn"
            onClick={saveAction.onSave}
            disabled={saveAction.saving || !saveAction.dirty}
            title="Save (Ctrl+S)"
          >
            {saveAction.saving ? "Saving…" : "Save"}
          </button>
        ) : null}

        <div className="app-header-divider" />

        {isRemote() && onWatchWindowId ? (
          <>
            <RemoteWindowSelect value={watchWindowId} onChange={onWatchWindowId} />
            <RemoteViewControls hwnd={watchWindowId} />
          </>
        ) : null}

        {isRemote() ? null : (
        <div className="window-controls">
          <button type="button" onClick={handleMinimize} className="window-control-btn no-drag" title="Minimize">
            <Icons.Minimize />
          </button>
          <button
            type="button"
            onClick={() => void handleMaximize()}
            className="window-control-btn no-drag"
            title={isMaximized ? "Restore" : "Maximize"}
          >
            {isMaximized ? <Icons.WindowRestore /> : <Icons.Maximize />}
          </button>
          <button
            type="button"
            onClick={handleClose}
            className="window-control-btn is-close no-drag"
            title={isFocus ? "Close" : "Hide to tray"}
          >
            <Icons.Close />
          </button>
        </div>
        )}
      </div>
    </header>
  );
}
