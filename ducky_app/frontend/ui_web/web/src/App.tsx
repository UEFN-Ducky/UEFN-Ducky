import { useCallback, useEffect, useState } from "react";
import { ConfirmModalProvider } from "./contexts/ConfirmModalContext";
import { installTerminalApprovals } from "./terminal/terminalApprovals";
import { EditorWorkspaceBridgeProvider } from "./contexts/EditorWorkspaceBridge";
import { QuickOpenBridgeProvider } from "./contexts/QuickOpenBridge";
import { AskAiBridgeProvider } from "./contexts/AskAiBridge";
import { ProblemsDuckyBridgeProvider } from "./contexts/ProblemsDuckyBridge";
import { AppearanceProvider } from "./theme/AppearanceContext";
import { ThemeProvider } from "./theme/ThemeProvider";
import { AppearanceFxBridge } from "./theme/AppearanceFxBridge";
import { AppearanceCssBridge } from "./theme/AppearanceCssBridge";
import { AppearanceSkinBridge } from "./theme/AppearanceSkinBridge";
import { SoundFxBridge } from "./sfx/SoundFxBridge";
import { MicPermissionModal } from "./voice/MicPermissionModal";
import { loadAudioSettings } from "./voice/audioSettings";
import { loadFollowCodeSettings } from "./verse-editor/queue/followCodeSettings";
import { DuckyCatalogProvider } from "./components/ducky/DuckyCatalogContext";
import { AppHeaderActionsProvider } from "./contexts/AppHeaderActionsContext";
import { VerseDiagnosticsSettingsProvider } from "./contexts/VerseDiagnosticsSettingsContext";
import { TerminalsSettingsProvider } from "./contexts/TerminalsSettingsContext";
import { ProjectFilesSettingsProvider } from "./contexts/ProjectFilesSettingsContext";
import { Header } from "./components/Header";
import { RemoteWindowLaunching, RemoteWindowOverlay, useUeFnLaunching } from "./components/RemoteWindowView";
import { RemoteWindowSender } from "./components/RemoteWindowSender";
import { installDirectPeer } from "./remote/directPeer";
import { isRemote } from "./hooks/usePanelApi";
import { onApiReady } from "./hooks/onApiReady";
import { WindowDrag } from "./components/WindowDrag";
import { WindowResize } from "./components/WindowResize";
import { ChatView } from "./views/ChatView";
import { FocusView } from "./views/FocusView";
import { SettingsView } from "./views/SettingsView";
import { useListenerStatus } from "./hooks/useListenerStatus";
import { useConnectionIcon } from "./hooks/useConnectionIcon";
import { useProject } from "./hooks/useProject";
import { useVersionCheck } from "./hooks/useVersionCheck";
import { useChatLayoutMode } from "./hooks/useChatLayoutMode";
import { useOsFileDropGuard } from "./hooks/useOsFileDropGuard";
import { installAgentEventBus } from "./hooks/useAgentEventBus";
import { applyGraphFocusPush } from "./hooks/graphActivity";
import { installPanelPushBus, subscribePanelPush } from "./hooks/usePanelPushBus";
import { queuePluginTrustRequest } from "./hooks/pluginTrustRequest";
import { UpdateAvailableModal } from "./components/UpdateAvailableModal";
import { UpdateLockOverlay } from "./components/UpdateLockOverlay";
import {
  NavigationHistoryProvider,
  useNavigationHistoryOptional,
  viewForHistory,
} from "./navigation/NavigationHistoryContext";
import { UndoHistoryProvider, useUndoHistoryOptional } from "./navigation/UndoHistoryContext";
import { useNavigationShortcuts } from "./navigation/useNavigationShortcuts";
import { useUndoShortcuts } from "./navigation/useUndoShortcuts";
import { registerCloseSettingsView, registerOpenSettingsView, requestOpenSettings } from "./navigation/openSettingsTab";
import { installDeepLinkListeners, peekStoreInstallRequest, requestOpenStore } from "./navigation/deepLinks";
import { installOpenFromParent } from "./remote/openFromParent";
import { nextChatLayoutMode, type ViewId } from "./types/panel";
import { WINDOW_ID } from "./tabs/tabRegistryClient";
import { persistDockSnapshot, readDockSnapshot } from "./workspace/workspaceDockStorage";
import { PluginShellBootBridge } from "./plugin-ui/shellBoot";
import { PluginCrashBanner } from "./plugin-ui/PluginCrashBanner";
import { installPluginFaultGuards } from "./plugin-ui/pluginCrashGuard";
import { useHydratePluginUiPrefs } from "./hooks/usePluginUiPrefs";
import { installModelsCatalogAutoRefresh } from "./hooks/modelsCatalogCache";
import { UiRpcBridge } from "./ui-targets/UiRpcBridge";
import { WalkthroughHost } from "./walkthrough";
import { ShowMeLayer } from "./showme/ShowMeLayer";
import { GatewaySetupNotice } from "./components/GatewaySetupNotice";
// App-level ErrorBoundary lives in main.tsx.

import { decodeFocusParam } from "./hooks/useFocusWindow";

const focusId = decodeFocusParam(new URLSearchParams(window.location.search).get("focus") ?? "");
const NOOP = () => {};

function PluginPrefsHydrate() {
  useHydratePluginUiPrefs();
  return null;
}
/** App-level wiring for history + undo gestures and view apply on back/forward. */
/** Agent enable of AI/local plugin → open Store + queue user trust confirm. */
/** Chat save/delete of a workflow opens the Workflows editor immediately. */
function GraphFocusBridge() {
  useEffect(() => {
    installAgentEventBus();
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type === "graph_focus") applyGraphFocusPush(event);
    });
  }, []);
  return null;
}

function PluginTrustBridge() {
  useEffect(() => {
    // HTTP poll must run even before ChatView mounts — Store enable/uninstall
    // pushes uefn_plugins_changed over /__panel_events.
    installAgentEventBus();
    installPanelPushBus();
    return subscribePanelPush((event) => {
      if (event.type !== "uefn_plugin_trust_request") return;
      const pluginId = (event.plugin_id || "").trim();
      if (!pluginId) return;
      queuePluginTrustRequest({
        pluginId,
        source: event.source || "ai",
        detail: event.detail || "",
      });
      requestOpenSettings("Store");
    });
  }, []);
  return null;
}

function AppShortcutsBridge({
  setView,
  hasProject,
}: {
  setView: (v: ViewId) => void;
  hasProject: boolean;
}) {
  const nav = useNavigationHistoryOptional();
  const undo = useUndoHistoryOptional();
  useNavigationShortcuts(nav?.back ?? NOOP, nav?.forward ?? NOOP);
  useUndoShortcuts(undo?.undo ?? NOOP, undo?.redo ?? NOOP);
  useEffect(() => {
    nav?.registerViewApplier((view) => setView(viewForHistory(view, hasProject)));
    return () => nav?.registerViewApplier(null);
  }, [nav, setView, hasProject]);
  return null;
}

export default function App() {
  // Window-wide: never let an OS-file drop reach WebView2's default handler
  // (which pops the Windows "open with" dialog). Runs in focus windows too.
  useOsFileDropGuard();
  useEffect(() => installPluginFaultGuards(), []);
  // Agent command cards (in their chat, or the header list) in every window, focus ones too.
  useEffect(() => { installTerminalApprovals(); }, []);
  if (focusId) {
    return <FocusView focusId={focusId} />;
  }
  const [currentView, setCurrentView] = useState<ViewId>("chat");
  const [watchWindowId, setWatchWindowId] = useState("");
  const uefnLaunching = useUeFnLaunching();
  // Desktop only: answer direct (tunnel-free) Remote View offers.
  useEffect(() => {
    if (isRemote()) return;
    return installDirectPeer();
  }, []);
  const { mode: layoutMode, setMode: setLayoutMode } = useChatLayoutMode();
  const [sidebarRefresh, setSidebarRefresh] = useState(0);
  const [projectRefresh, setProjectRefresh] = useState(0);
  const listener = useListenerStatus(8000, projectRefresh);
  useConnectionIcon(listener);

  const bumpSidebar = useCallback(() => {
    setSidebarRefresh((n) => n + 1);
    setProjectRefresh((n) => n + 1);
  }, []);

  const project = useProject(15000, projectRefresh, bumpSidebar);
  const versionCheck = useVersionCheck();

  const hasProject = !!project.path?.trim();

  const cycleLayoutMode = useCallback(() => {
    const snapshot = readDockSnapshot(WINDOW_ID);
    const next = nextChatLayoutMode(layoutMode, snapshot.leftRailOpen);
    setLayoutMode(next);
    persistDockSnapshot({ ...snapshot, leftRailOpen: next === "full" }, WINDOW_ID);
  }, [layoutMode, setLayoutMode]);

  useEffect(() => {
    if (!hasProject || currentView !== "settings") return;
    // A project keeps Settings as an editor tab. A full settings page unmounts both rails.
    setCurrentView("chat");
  }, [hasProject, currentView]);

  useEffect(() => {
    return registerOpenSettingsView(() => {
      setCurrentView((view) => (view === "settings" ? view : "settings"));
    });
  }, []);

  useEffect(() => registerCloseSettingsView(() => setCurrentView("chat")), []);

  useEffect(() => {
    void loadAudioSettings();
    void loadFollowCodeSettings();
  }, []);

  // uefn-ducky:// deep links (website Store "Install in app") — live + cold start.
  useEffect(() => {
    return installDeepLinkListeners();
  }, []);

  // Remote View on the phone: a tapped "agent finished" notification opens that chat or workflow.
  useEffect(() => {
    return installOpenFromParent();
  }, []);

  // Signed-out launch: Plugins library → Ducky Account details (do not auto-start pairing).
  useEffect(() => {
    if (isRemote()) return;
    return onApiReady((api) => {
      void (async () => {
        try {
          const row = await api.duckyos_get_status?.();
          if (!row || row.logged_in) return;
          if (peekStoreInstallRequest()) return;
          requestOpenStore({ slug: "account" });
        } catch {
          /* ignore */
        }
      })();
    });
  }, []);

  // Gateway Install/Enable/Disable → refresh Ducky model picker without restart.
  useEffect(() => {
    installModelsCatalogAutoRefresh();
  }, []);

  const settingsView = <SettingsView version={listener.version} />;

  const mainContent = currentView === "settings" ? (
    settingsView
  ) : (
    <ChatView
      layoutMode={layoutMode}
      sidebarRefresh={sidebarRefresh}
      projectSlug={project.slug}
      projectPath={project.path}
    />
  );

  return (
    <ConfirmModalProvider>
    <EditorWorkspaceBridgeProvider>
    <AskAiBridgeProvider>
    <ProblemsDuckyBridgeProvider>
    <QuickOpenBridgeProvider>
    <AppearanceProvider>
      <DuckyCatalogProvider>
      <AppHeaderActionsProvider>
      <TerminalsSettingsProvider>
      <ProjectFilesSettingsProvider>
      <VerseDiagnosticsSettingsProvider>
      <ThemeProvider />
      <AppearanceCssBridge />
      <AppearanceFxBridge />
      <AppearanceSkinBridge />
      <SoundFxBridge />
      <WindowResize />
      <WindowDrag />
      <NavigationHistoryProvider>
      <UndoHistoryProvider>
      <AppShortcutsBridge setView={setCurrentView} hasProject={hasProject} />
      <PluginTrustBridge />
      <GraphFocusBridge />
      <PluginPrefsHydrate />
      <PluginShellBootBridge />
      <UiRpcBridge />
      <WalkthroughHost />
      <ShowMeLayer />
      <GatewaySetupNotice />
      <div className="app-container">
        <div id="ducky-skin-frame" className="ducky-skin-slot ducky-skin-slot--frame" aria-hidden="true" />
        <PluginCrashBanner />
        <Header
          currentView={currentView}
          setView={setCurrentView}
          isOnline={listener.online}
          isWedged={listener.wedged}
          statusText={listener.status_text}
          listenerStatus={listener}
          projectMatch={listener.project_match}
          uefnProjectName={listener.uefn_project_name}
          project={project}
          layoutMode={layoutMode}
          cycleLayoutMode={cycleLayoutMode}
          hasProject={hasProject}
          showQuickOpen={hasProject}
          onProjectChanged={bumpSidebar}
          watchWindowId={watchWindowId}
          onWatchWindowId={setWatchWindowId}
        />

        <main className="app-main">{mainContent}</main>
        {!isRemote() ? <RemoteWindowSender /> : null}
        {watchWindowId ? (
          <RemoteWindowOverlay hwnd={watchWindowId} />
        ) : uefnLaunching ? (
          <RemoteWindowLaunching label={uefnLaunching} />
        ) : null}
      </div>
      {versionCheck.status?.remote_version && (
        <UpdateAvailableModal
          open={versionCheck.showModal}
          status={versionCheck.status}
          onDismiss={versionCheck.dismiss}
        />
      )}
      <UpdateLockOverlay />
      <MicPermissionModal />
      </UndoHistoryProvider>
      </NavigationHistoryProvider>
      </VerseDiagnosticsSettingsProvider>
      </ProjectFilesSettingsProvider>
      </TerminalsSettingsProvider>
      </AppHeaderActionsProvider>
      </DuckyCatalogProvider>
    </AppearanceProvider>
    </QuickOpenBridgeProvider>
    </ProblemsDuckyBridgeProvider>
    </AskAiBridgeProvider>
    </EditorWorkspaceBridgeProvider>
    </ConfirmModalProvider>
  );
}
