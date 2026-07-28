import { useEffect, useMemo, useState } from "react";
import { Icons } from "../icons/Icons";
import { ProjectSelector } from "./ProjectSelector";
import { ConnectionStatusIcon } from "./ConnectionStatusIcon";
import { VerseProblemsDropdown } from "./VerseProblemsDropdown";
import { TerminalHeaderDropdown } from "../terminal/TerminalHeaderDropdown";
import { DiscordHeaderDropdown } from "./groupchat/DiscordHeaderDropdown";
import { LanguageHeaderDropdown } from "./LanguageHeaderDropdown";
import { PluginSurfaceBoundary } from "../plugin-ui/PluginSurfaceBoundary";
import { useAppHeaderActions } from "../contexts/AppHeaderActionsContext";
import { useNavigationHistoryOptional } from "../navigation/NavigationHistoryContext";
import { useRightRailOpen } from "../hooks/useRightRailOpen";
import { useAppearance } from "../theme/AppearanceContext";
import { QuickOpenBar } from "./quick-open/QuickOpenBar";
import type { ChatLayoutMode, ProjectInfo, ViewId } from "../types/panel";
import { getApi } from "../hooks/usePanelApi";
import { requestOpenSettings } from "../navigation/openSettingsTab";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { useDiscordUiPrefs } from "../hooks/usePluginUiPrefs";
import { useStoreUpdateBadge } from "../hooks/useStoreUpdateBadge";
import { useDiscordTabOpen } from "../navigation/openDiscordTab";
import {
  resolvePluginHeaderAction,
  resolvePluginHeaderIcon,
  sortPluginHeaderButtons,
} from "../hooks/pluginHeaderActions";
import { useUiTarget } from "../ui-targets/registry";

interface HeaderProps {
  variant?: "main" | "focus";
  currentView?: ViewId;
  setView?: (v: ViewId) => void;
  isOnline: boolean;
  isWedged?: boolean;
  statusText?: string;
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
}

function statusTooltip(
  isOnline: boolean,
  isWedged: boolean,
  statusText: string | undefined,
  project: ProjectInfo,
  projectMatch: boolean | undefined,
): string {
  const projectName = project.path?.trim() ? project.name : null;

  if (statusText?.trim()) {
    if (isOnline && projectMatch === false && projectName) {
      return `${statusText} — open ${projectName} in UEFN to use this project`;
    }
    return statusText;
  }
  if (isWedged) return "Listener wedged — restart UEFN (commands not processing)";
  if (!isOnline) {
    return projectName
      ? `Offline — open ${projectName} in UEFN to connect`
      : "Offline — open UEFN + deploy listener";
  }
  return "Online";
}

const LAYOUT_TOGGLE_META: Record<ChatLayoutMode, { title: string; Icon: () => JSX.Element }> = {
  full: { title: "Hide sidebar", Icon: Icons.PanelLeft },
  sidebarHidden: { title: "Show sidebar", Icon: Icons.PanelLeftClose },
};

const RIGHT_RAIL_TOGGLE_META = {
  open: { title: "Hide right sidebar", Icon: Icons.PanelRight },
  closed: { title: "Show right sidebar", Icon: Icons.PanelRightClose },
} as const;

export function Header({
  variant = "main",
  currentView = "chat",
  setView,
  isOnline,
  isWedged = false,
  statusText,
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
}: HeaderProps) {
  const [isMaximized, setIsMaximized] = useState(false);
  const [isDuckyHovered, setIsDuckyHovered] = useState(false);
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

  // Keep the maximise/restore button in sync with the *actual* window state. The OS
  // maximises on a native caption double-click (and on Win+Up / Aero-snap) without routing
  // through handleMaximize, so button clicks alone can't be trusted — re-read the real state
  // whenever the window resizes (a maximise/restore always resizes the WebView viewport).
  useEffect(() => {
    const api = getApi();
    const readMaximized = api?.is_window_maximized?.bind(api);
    if (!readMaximized) return;
    let cancelled = false;
    let throttle: ReturnType<typeof setTimeout> | null = null;

    const sync = () => {
      void readMaximized()
        .then((max) => {
          if (!cancelled) setIsMaximized(!!max);
        })
        .catch(() => {});
    };

    const onResize = () => {
      if (throttle) return; // coalesce a resize-drag burst
      sync(); // leading edge → instant flip on double-click maximise/restore
      throttle = setTimeout(() => {
        throttle = null;
        sync(); // trailing edge → settle to the final state
      }, 150);
    };

    sync(); // initial: window may already be maximised (e.g. restored session)
    window.addEventListener("resize", onResize);
    return () => {
      cancelled = true;
      if (throttle) clearTimeout(throttle);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  const tooltip = statusTooltip(isOnline, isWedged, statusText, project, projectMatch);
  const nav = useNavigationHistoryOptional();
  const showNav = !isFocus && !!nav;
  const sidebarEnabled = (isFocus || !isSettingsOverlay) && hasProject;
  const headerActions = useAppHeaderActions();
  const { rightRailOpen, hasRightPanels, toggleRightRail } = useRightRailOpen();
  const pluginContrib = usePluginContributions();
  const { prefs: discordUiPrefs } = useDiscordUiPrefs();
  const { hasUpdates: hasStoreUpdates } = useStoreUpdateBadge();
  const discordTabOpen = useDiscordTabOpen();
  const pluginHeaderButtons = useMemo(() => {
    if (!hasProject || isFocus || isSettingsOverlay) return [];
    return sortPluginHeaderButtons(pluginContrib.header_buttons).filter((btn) => {
      if (!resolvePluginHeaderAction(btn.action, btn.plugin_id)) return false;
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
  const showEditorActions = isFocus || !isSettingsOverlay;
  const saveAction = showEditorActions ? headerActions.save : null;
  const workflowAction = showEditorActions && hasProject ? headerActions.verseWorkflow : null;
  const problemsAction = showEditorActions && hasProject ? headerActions.problems : null;
  const terminalAction = showEditorActions && hasProject ? headerActions.terminal : null;
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

  return (
    <header
      ref={headerTargetRef}
      className={`glass-panel app-header${isFocus ? " app-header--focus" : ""}${isSettingsOverlay ? " app-header--settings" : " drag-region app-drag-surface"}`}
    >
      <div id="ducky-skin-header" className="ducky-skin-slot ducky-skin-slot--header" aria-hidden="true" />
      <div className={`app-header-left${isSettingsOverlay ? " app-header-left--settings" : ""}`}>
        {!isSettingsOverlay ? (
          <>
          <div className="app-header-status-row">
            {isFocus ? (
              <span className="connection-status-btn connection-status-btn--readonly" title={tooltip}>
                <ConnectionStatusIcon isOnline={isOnline} isWedged={isWedged} />
              </span>
            ) : (
              <button
                ref={settingsTargetRef}
                type="button"
                className={`no-drag connection-status-btn${hasStoreUpdates ? " has-store-update" : ""}`}
                title={
                  isDuckyHovered
                    ? "Settings"
                    : hasStoreUpdates
                      ? `${tooltip} — Store updates available`
                      : tooltip
                }
                onClick={() => void handleSettingsToggle()}
                onMouseEnter={() => setIsDuckyHovered(true)}
                onMouseLeave={() => setIsDuckyHovered(false)}
              >
                {isDuckyHovered ? (
                  <span className="connection-status-gear">
                    <Icons.Settings />
                  </span>
                ) : (
                  <ConnectionStatusIcon isOnline={isOnline} isWedged={isWedged} />
                )}
                {hasStoreUpdates ? (
                  <span className="store-update-dot" aria-label="Store updates available" />
                ) : null}
              </button>
            )}
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
        <div className="app-header-center no-drag">
          {navButtons}
          <button
            type="button"
            onClick={cycleLayoutMode}
            className={`icon-btn no-drag sidebar-toggle-btn sidebar-toggle-btn--${layoutMode} ${sidebarEnabled ? "" : "is-disabled"}`}
            title={layoutToggle.title}
          >
            <LayoutToggleIcon />
          </button>
          {showQuickOpen ? <QuickOpenBar /> : null}
          <button
            type="button"
            onClick={toggleRightRail}
            className={`icon-btn no-drag sidebar-toggle-btn sidebar-toggle-btn--right sidebar-toggle-btn--${rightRailOpen ? "full" : "sidebarHidden"} ${rightSidebarEnabled ? "" : "is-disabled"}`}
            title={rightRailToggle.title}
          >
            <RightRailToggleIcon />
          </button>
          {pluginHeaderButtons.map((btn) => {
            const pluginId = (btn.plugin_id || btn.id || "plugin").trim().toLowerCase();
            const key = `${pluginId}:${btn.id}`;
            const isDiscord =
              btn.plugin_id === "discord" ||
              btn.id === "discord" ||
              btn.action === "builtin:open-discord";
            if (isDiscord) {
              return (
                <PluginSurfaceBoundary
                  key={key}
                  pluginId={pluginId || "discord"}
                  surface="header-button"
                  compact
                >
                  <DiscordHeaderDropdown
                    icon={resolvePluginHeaderIcon(btn.icon)}
                    title={btn.title || btn.id}
                    active={discordTabOpen}
                  />
                </PluginSurfaceBoundary>
              );
            }
            const isTranslation =
              btn.plugin_id === "translation" || btn.id === "translation";
            if (isTranslation) {
              return (
                <PluginSurfaceBoundary
                  key={key}
                  pluginId={pluginId || "translation"}
                  surface="header-button"
                  compact
                >
                  <LanguageHeaderDropdown
                    icon={resolvePluginHeaderIcon(btn.icon)}
                    title={btn.title || btn.id}
                  />
                </PluginSurfaceBoundary>
              );
            }
            const onClick = resolvePluginHeaderAction(btn.action, btn.plugin_id);
            if (!onClick) return null;
            return (
              <PluginSurfaceBoundary key={key} pluginId={pluginId} surface="header-button" compact>
                <button
                  type="button"
                  className="icon-btn no-drag plugin-header-btn"
                  title={btn.title || btn.id}
                  aria-label={btn.title || btn.id}
                  onClick={onClick}
                >
                  {resolvePluginHeaderIcon(btn.icon)}
                </button>
              </PluginSurfaceBoundary>
            );
          })}
        </div>
      ) : showQuickOpen ? (
        <div className="app-header-center no-drag">
          <QuickOpenBar />
        </div>
      ) : null}

      <div className={`app-header-trailing${isSettingsOverlay ? " app-header-trailing--settings" : ""}`}>
        {showWorkflow || terminalAction || problemsAction ? (
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
            {isMaximized ? <Icons.Restore /> : <Icons.Maximize />}
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
      </div>
    </header>
  );
}
