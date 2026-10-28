import { useEffect, useMemo, useRef, useState } from "react";
import { Icons } from "../icons/Icons";
import { ProjectSelector } from "./ProjectSelector";
import { ConnectionStatusDropdown } from "./ConnectionStatusDropdown";
import { BackgroundActivityDropdown } from "./BackgroundActivityDropdown";
import { LiveHeaderDropdown } from "./LiveHeaderDropdown";
import { VerseProblemsDropdown } from "./VerseProblemsDropdown";
import { TerminalHeaderDropdown } from "../terminal/TerminalHeaderDropdown";
import { SandboxAppsDropdown } from "./SandboxAppsDropdown";
import { LanguageHeaderDropdown } from "./LanguageHeaderDropdown";
import { PluginSurfaceBoundary } from "../plugin-ui/PluginSurfaceBoundary";
import { useAppHeaderActions, useProblemsMenuOpen } from "../contexts/AppHeaderActionsContext";
import { useNavigationHistoryOptional } from "../navigation/NavigationHistoryContext";
import { useRightRailOpen } from "../hooks/useRightRailOpen";
import { useNarrowLayout } from "../hooks/useNarrowLayout";
import { useOverlayRailSession } from "../hooks/useOverlayRailSession";
import { toggleOverlayRail } from "../workspace/overlayRailSession";
import { QuickOpenBar } from "./quick-open/QuickOpenBar";
import { useQuickOpenBridge } from "../contexts/QuickOpenBridge";
import type { ChatLayoutMode, ListenerStatus, ProjectInfo } from "../types/panel";
import { getApi, isRemote } from "../hooks/usePanelApi";
import { isNativeWindowChrome } from "../utils/nativeWindowChrome";
import { requestOpenSettings } from "../navigation/openSettingsTab";
import { requestOpenChangesTab } from "../navigation/openChangesTab";
import { requestOpenWorkflowsTab } from "../navigation/openWorkflowsTab";
import { usePluginContributions } from "../hooks/usePluginContributions";
import { useHeaderVisibility } from "../hooks/useHeaderVisibility";
import { useStoreUpdateBadge } from "../hooks/useStoreUpdateBadge";
import {
  headerButtonCatalog,
  pluginHeaderButtonId,
  pluginHeaderTargetId,
} from "../workspace/headerVisibilityStorage";
import {
  listedPluginHeaderButtons,
  resolvePluginHeaderAction,
  resolvePluginHeaderIcon,
} from "../hooks/pluginHeaderActions";
import { ContextMenu, useContextMenuState, type ContextMenuItem } from "./ContextMenu";
import { targetRef, useUiTarget } from "../ui-targets/registry";
import { ChoiceTriggerFace } from "./ChoiceDropdown";
import { DropdownPanel } from "./DropdownPanel";
import { setHeaderTabsSlot } from "./MobileTabSwitcher";
import { RemoteWindowSelect } from "./RemoteWindowView";
import type { PluginHeaderButton } from "../hooks/usePluginContributions";

interface HeaderProps {
  variant?: "main" | "focus";
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
  // Show me and plugin tours point at it as header.button.<plugin>.<button>.
  const tourRef = targetRef(pluginHeaderTargetId(pluginId, btn.id), { kind: "button", label: title, route: "chat" });
  if (layout === "row") {
    return (
      <PluginSurfaceBoundary key={key} pluginId={pluginId} surface="header-button" compact>
        <button
          ref={tourRef}
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
        ref={tourRef}
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

function HeaderToolsMenu({
  canBack,
  canForward,
  onBack,
  onForward,
  showNav,
  layoutTitle,
  onCycleLayout,
  sidebarEnabled,
  showLeftToggle,
  rightTitle,
  onToggleRight,
  rightEnabled,
  showRightToggle,
  showWorkflow,
  onCompile,
  onPush,
  canPush,
  compileBusy,
  onSearch,
  onProblems,
  onTerminal,
  onLedger,
  onAutomations,
  showSearch,
}: {
  canBack: boolean;
  canForward: boolean;
  onBack: () => void;
  onForward: () => void;
  showNav: boolean;
  layoutTitle: string;
  onCycleLayout: () => void;
  sidebarEnabled: boolean;
  showLeftToggle: boolean;
  rightTitle: string;
  onToggleRight: () => void;
  rightEnabled: boolean;
  showRightToggle: boolean;
  showWorkflow: boolean;
  onCompile?: () => void;
  onPush?: () => void;
  canPush?: boolean;
  compileBusy?: boolean;
  onSearch?: () => void;
  onProblems?: () => void;
  onTerminal?: () => void;
  onLedger?: () => void;
  onAutomations?: () => void;
  showSearch?: boolean;
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
    <div className="choice-dropdown choice-dropdown--compact choice-dropdown--icon choice-dropdown--light header-tools-menu no-drag">
      <button
        ref={anchorRef}
        type="button"
        className={`choice-dropdown-trigger${open ? " is-open" : ""}`}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label="Tools"
        title="Tools"
        onClick={() => setOpen((v) => !v)}
      >
        <ChoiceTriggerFace icon={<Icons.Sliders />} />
      </button>
      <DropdownPanel open={open} anchorRef={anchorRef} onClose={() => setOpen(false)} minWidth={200}>
        <div className="plugin-header-menu-list" role="menu">
          {showNav ? item("Back", onBack, !canBack) : null}
          {showNav ? item("Forward", onForward, !canForward) : null}
          {showLeftToggle
            ? item(layoutTitle, sidebarEnabled ? onCycleLayout : undefined, !sidebarEnabled)
            : null}
          {showRightToggle
            ? item(rightTitle, rightEnabled ? onToggleRight : undefined, !rightEnabled)
            : null}
          {showSearch ? item("Search", onSearch) : null}
          {showWorkflow ? item("Build Verse", onCompile, compileBusy) : null}
          {showWorkflow && canPush ? item("Push Verse", onPush, compileBusy) : null}
          {item("Problems", onProblems)}
          {item("Terminal", onTerminal)}
          {item("Workflows", onAutomations)}
          {item("Ledger", onLedger)}
        </div>
      </DropdownPanel>
    </div>
  );
}

function PluginHeaderMenu({ buttons }: { buttons: PluginHeaderButton[] }) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  if (!buttons.length) return null;
  return (
    <div className="choice-dropdown choice-dropdown--compact choice-dropdown--icon choice-dropdown--light plugin-header-menu no-drag">
      <button
        ref={anchorRef}
        type="button"
        className={`choice-dropdown-trigger${open ? " is-open" : ""}`}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label="Plugins"
        title="Plugins"
        onClick={() => setOpen((v) => !v)}
      >
        <ChoiceTriggerFace icon={<Icons.Puzzle />} />
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

export function Header({
  variant = "main",
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

  const handleSettingsToggle = () => {
    requestOpenSettings();
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
  const headerActions = useAppHeaderActions();
  const { leftRailOpen, rightRailOpen, hasRightPanels, toggleRightRail, leftRailEnabled, rightRailEnabled } =
    useRightRailOpen();
  const overlaySession = useOverlayRailSession();
  const pluginContrib = usePluginContributions();
  const { isVisible: headerBtnVisible, setVisible: setHeaderBtnVisible } = useHeaderVisibility();
  const { hasUpdates: hasStoreUpdates } = useStoreUpdateBadge();
  const narrowHeader = useNarrowLayout();
  const listedHeaderButtons = useMemo(
    () => (!hasProject || isFocus ? [] : listedPluginHeaderButtons(pluginContrib.header_buttons)),
    [hasProject, isFocus, pluginContrib.header_buttons],
  );
  const pluginHeaderButtons = useMemo(
    () =>
      listedHeaderButtons.filter((btn) =>
        headerBtnVisible(pluginHeaderButtonId(btn.plugin_id || btn.id, btn.id)),
      ),
    [headerBtnVisible, listedHeaderButtons],
  );
  const headerMenu = useContextMenuState<null>();
  const compactHeader = narrowHeader;
  const pluginHeader = compactHeader ? (
    <PluginHeaderMenu buttons={pluginHeaderButtons} />
  ) : (
    pluginHeaderButtons.map((btn) => (
      <PluginHeaderItem key={`${btn.plugin_id || btn.id}:${btn.id}`} btn={btn} layout="icon" />
    ))
  );
  const saveAction = headerActions.save;
  const workflowAction = hasProject ? headerActions.verseWorkflow : null;
  const problemsAction = hasProject ? headerActions.problems : null;
  const terminalAction = hasProject ? headerActions.terminal : null;
  // The ledger is always reachable once a project is open, even with no other editor action.
  const showChanges = hasProject;
  const overlayLeftOpen = overlaySession.left;
  const overlayRightOpen = overlaySession.right;
  const leftRailShown = compactHeader
    ? overlayLeftOpen
    : layoutMode !== "sidebarHidden" && leftRailOpen;
  const rightRailShown = compactHeader ? overlayRightOpen : rightRailOpen;
  const layoutToggle = LAYOUT_TOGGLE_META[leftRailShown ? "full" : "sidebarHidden"];
  const LayoutToggleIcon = layoutToggle.Icon;
  const rightRailToggle = RIGHT_RAIL_TOGGLE_META[rightRailShown ? "open" : "closed"];
  const RightRailToggleIcon = rightRailToggle.Icon;
  const rightSidebarEnabled = hasRightPanels;
  const showNavButtons = showNav && headerBtnVisible("nav");
  const showSearch = !!showQuickOpen && headerBtnVisible("search");
  const showLeftSidebarToggle = leftRailEnabled && headerBtnVisible("leftSidebar");
  const showRightSidebarToggle = rightRailEnabled && headerBtnVisible("rightSidebar");
  // Right-click on the icon area: a switch per header button, hidden ones included,
  // the same list as Appearance → Header. Built-ins that can't show here are left out.
  const builtinApplies: Record<string, boolean> = {
    nav: showNav,
    leftSidebar: leftRailEnabled,
    rightSidebar: rightRailEnabled,
    search: !!showQuickOpen,
  };
  const builtinIcons: Record<string, JSX.Element> = {
    nav: <Icons.ChevronLeft />,
    leftSidebar: <Icons.PanelLeft />,
    rightSidebar: <Icons.PanelRight />,
    search: <Icons.Search />,
  };
  const pluginIcons = new Map(
    listedHeaderButtons.map((btn) => [pluginHeaderButtonId(btn.plugin_id || btn.id, btn.id), btn.icon]),
  );
  const headerMenuItems: ContextMenuItem[] = headerButtonCatalog(listedHeaderButtons)
    .filter((row) => builtinApplies[row.id] ?? true)
    .map((row) => {
      const on = headerBtnVisible(row.id);
      return {
        id: row.id,
        label: row.label,
        icon: builtinIcons[row.id] ?? (pluginIcons.has(row.id) ? resolvePluginHeaderIcon(pluginIcons.get(row.id)) : <Icons.Puzzle />),
        switch: true,
        checked: on,
        keepOpen: true,
        onClick: () => setHeaderBtnVisible(row.id, !on),
      };
    });
  const onCycleLayout = () => {
    if (compactHeader) {
      toggleOverlayRail("left");
      return;
    }
    cycleLayoutMode();
  };
  const onToggleRight = () => {
    if (compactHeader) {
      toggleOverlayRail("right");
      return;
    }
    toggleRightRail();
  };

  const { setProblemsMenuOpen } = useProblemsMenuOpen();
  const { openPalette } = useQuickOpenBridge();
  const navButtons = showNavButtons ? (
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
  // Watching a PC window from the phone: only Ducky and the View menu stay up top.
  const streaming = isRemote() && !!watchWindowId;
  const headerTargetRef = useUiTarget("shell.header", { kind: "button", label: "Top bar", route: "chat" });
  const settingsTargetRef = useUiTarget("header.settings", {
    kind: "button",
    label: "Settings",
    route: "settings",
  });
  const workflowsTargetRef = useUiTarget("header.automations", {
    kind: "button",
    label: "Workflows",
    route: "workflows",
  });
  const changesTargetRef = useUiTarget("header.changes", {
    kind: "button",
    label: "Ledger",
    route: "changes",
  });

  return (
    <header
      ref={headerTargetRef}
      className={`glass-panel app-header${isFocus ? " app-header--focus" : ""} drag-region app-drag-surface${compactHeader ? " app-header--compact" : ""}${isRemote() ? " app-header--remote" : ""}${streaming ? " app-header--streaming" : ""}`}
    >
      <div id="ducky-skin-header" className="ducky-skin-slot ducky-skin-slot--header" aria-hidden="true" />
      <div className="app-header-left">
          <div className="app-header-status-row">
            <span ref={settingsTargetRef}>
              <ConnectionStatusDropdown
                status={connectionStatus}
                projectName={project.path?.trim() ? project.name : undefined}
                hasStoreUpdates={hasStoreUpdates}
                onOpenSettings={() => {
                  // Settings is part of Ducky: show Ducky instead of the PC window.
                  if (streaming) onWatchWindowId?.("");
                  handleSettingsToggle();
                }}
                extra={
                  !isFocus && isRemote() ? (
                    <ProjectSelector
                      embedded
                      project={project}
                      onProjectChanged={onProjectChanged}
                      uefnProjectName={uefnProjectName}
                      projectMatch={projectMatch}
                      listenerOnline={isOnline}
                    />
                  ) : null
                }
              />
            </span>
            {isFocus ? (
              project.path?.trim() ? (
                <span className="app-header-project-label" title={project.path}>
                  {project.name}
                </span>
              ) : null
            ) : isRemote() ? null : (
              <ProjectSelector
                project={project}
                onProjectChanged={onProjectChanged}
                uefnProjectName={uefnProjectName}
                projectMatch={projectMatch}
                listenerOnline={isOnline}
              />
            )}
          </div>
      </div>

      {isFocus ? (
        <div className="app-header-drag-fill drag-region app-drag-surface" aria-hidden="true" />
      ) : null}

      {!isFocus ? (
        <div
          className="app-header-center drag-region app-drag-surface"
          data-header-menu
          onContextMenu={(e) => headerMenu.open(e, null)}
        >
          {compactHeader ? (
            <>
            <span className="app-header-tabs-slot no-drag" ref={setHeaderTabsSlot} />
            {showSearch ? <QuickOpenBar /> : null}
            <HeaderToolsMenu
              showNav={showNavButtons}
              canBack={!!nav?.canBack}
              canForward={!!nav?.canForward}
              onBack={() => nav?.back()}
              onForward={() => nav?.forward()}
              layoutTitle={layoutToggle.title}
              onCycleLayout={onCycleLayout}
              sidebarEnabled
              showLeftToggle={showLeftSidebarToggle}
              rightTitle={rightRailToggle.title}
              onToggleRight={onToggleRight}
              rightEnabled={rightSidebarEnabled}
              showRightToggle={showRightSidebarToggle}
              showWorkflow={showWorkflow}
              onCompile={workflowAction?.onCompile}
              onPush={workflowAction?.onPush}
              canPush={workflowAction?.canPush}
              compileBusy={workflowAction?.busy || workflowAction?.buildState === 3}
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
              onAutomations={() => requestOpenWorkflowsTab()}
              showSearch={showSearch}
              onSearch={showSearch ? () => openPalette("file") : undefined}
            />
            </>
          ) : (
            <>
              {navButtons}
              {showSearch ? <QuickOpenBar /> : null}
              {showLeftSidebarToggle ? (
                <button
                  type="button"
                  onClick={onCycleLayout}
                  className={`icon-btn no-drag sidebar-toggle-btn sidebar-toggle-btn--${layoutMode}`}
                  title={layoutToggle.title}
                >
                  <LayoutToggleIcon />
                </button>
              ) : null}
              {showRightSidebarToggle ? (
                <button
                  type="button"
                  onClick={onToggleRight}
                  className={`icon-btn no-drag sidebar-toggle-btn sidebar-toggle-btn--right sidebar-toggle-btn--${rightRailOpen ? "full" : "sidebarHidden"} ${rightSidebarEnabled ? "" : "is-disabled"}`}
                  title={rightRailToggle.title}
                >
                  <RightRailToggleIcon />
                </button>
              ) : null}
            </>
          )}
          {pluginHeader}
        </div>
      ) : showSearch ? (
        <div className="app-header-center drag-region app-drag-surface">
          <QuickOpenBar />
        </div>
      ) : null}

      <div className="app-header-trailing">
        {streaming ? null : (
        <>
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
          <SandboxAppsDropdown />
          <button
              ref={workflowsTargetRef}
              type="button"
              className="icon-btn no-drag"
              title="Workflows"
              aria-label="Open Workflows"
              onClick={() => requestOpenWorkflowsTab()}
            >
              <Icons.GitBranch />
          </button>
          {showChanges ? (
            <button
              ref={changesTargetRef}
              type="button"
              className="icon-btn app-header-changes-btn"
              title="Ledger — everything the AI changed, and how to undo it"
              aria-label="Open ledger"
              onClick={() => requestOpenChangesTab()}
            >
              <Icons.Diff />
            </button>
          ) : null}
        </span>
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

        <LiveHeaderDropdown />
        <BackgroundActivityDropdown />

        <div className="app-header-divider" />
        </>
        )}

        {isRemote() && onWatchWindowId ? (
          <RemoteWindowSelect
            value={watchWindowId}
            onChange={onWatchWindowId}
            projectName={project.path?.trim() ? project.name : undefined}
          />
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
      {headerMenu.menu ? (
        <ContextMenu
          x={headerMenu.menu.x}
          y={headerMenu.menu.y}
          items={headerMenuItems}
          onClose={headerMenu.close}
        />
      ) : null}
    </header>
  );
}
