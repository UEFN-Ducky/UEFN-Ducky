import { useCallback, useEffect, useMemo } from "react";

import { useRegisterAppHeaderActions } from "../contexts/AppHeaderActionsContext";

import { useTerminalsSettings } from "../contexts/TerminalsSettingsContext";

import type { EditorLayoutState, EditorTab } from "../types/panel";

import { findGroupForTab } from "../utils/editorLayoutOps";

import type { TerminalShell } from "../terminal/types";

import { useTerminalBusyStatuses } from "../terminal/useTerminalBusyStatuses";



interface TerminalHeaderBridgeProps {

  projectPath: string;

  openTabs: EditorTab[];

  /** Closed-but-alive sessions kept in the header list for reopen. */

  parkedTabs?: EditorTab[];

  layout: EditorLayoutState;

  defaultTerminalShell: TerminalShell;

  setDefaultTerminalShell: (shell: TerminalShell) => void;

  onNewTerminal: () => void;

  /** Fully kill/delete a terminal (header X). */

  onCloseTerminal: (tabId: string) => void;

  /** Optional override — reopen parked or focus an open tab. */

  onGotoTerminal?: (tabId: string) => void;

  activateTabInGroup: (groupId: string, tabId: string) => void;

  setFocusedGroup: (groupId: string) => void;

}



export function TerminalHeaderBridge({

  projectPath,

  openTabs,

  parkedTabs = [],

  layout,

  defaultTerminalShell,

  setDefaultTerminalShell,

  onNewTerminal,

  onCloseTerminal,

  onGotoTerminal,

  activateTabInGroup,

  setFocusedGroup,

}: TerminalHeaderBridgeProps) {

  const { enabled: terminalsEnabled } = useTerminalsSettings();

  const { setHeaderActions } = useRegisterAppHeaderActions();

  const hasProject = !!projectPath.trim();



  const focusedActiveTabId = layout.groups[layout.focusedGroupId]?.activeTabId ?? null;



  const openTerminalTabs = useMemo(

    () => openTabs.filter((t) => t.kind === "terminal" && t.terminalSessionId),

    [openTabs],

  );



  const terminalTabs = useMemo(() => {

    const openIds = new Set(openTerminalTabs.map((t) => t.id));

    const parkedOnly = parkedTabs.filter(

      (t) => t.kind === "terminal" && t.terminalSessionId && !openIds.has(t.id),

    );

    return [...openTerminalTabs, ...parkedOnly];

  }, [openTerminalTabs, parkedTabs]);



  const sessionIds = useMemo(

    () => terminalTabs.map((t) => t.terminalSessionId!).filter(Boolean),

    [terminalTabs],

  );



  const busyStatuses = useTerminalBusyStatuses(sessionIds);



  const terminals = useMemo(() => {
    const openIdSet = new Set(openTerminalTabs.map((t) => t.id));
    return terminalTabs.map((t) => {
      const sessionId = t.terminalSessionId!;
      const status = busyStatuses.get(sessionId);
      const isOpen = openIdSet.has(t.id);
      return {
        id: t.id,
        sessionId,
        name: t.name,
        shell: t.terminalShell ?? "bash",
        active: isOpen && t.id === focusedActiveTabId,
        parked: !isOpen,
        running: status?.running ?? false,
        runner: status?.runner ?? null,
      };
    });
  }, [terminalTabs, openTerminalTabs, focusedActiveTabId, busyStatuses]);



  const handleGotoTerminal = useCallback(

    (tabId: string) => {

      if (onGotoTerminal) {

        onGotoTerminal(tabId);

        return;

      }

      const groupId = findGroupForTab(layout, tabId);

      if (!groupId) return;

      setFocusedGroup(groupId);

      activateTabInGroup(groupId, tabId);

    },

    [onGotoTerminal, layout, setFocusedGroup, activateTabInGroup],

  );



  const handleCloseTerminal = useCallback(

    (tabId: string) => {

      onCloseTerminal(tabId);

    },

    [onCloseTerminal],

  );



  useEffect(() => {

    if (!terminalsEnabled || !hasProject) {

      setHeaderActions({ terminal: null });

      return;

    }

    setHeaderActions({

      terminal: {

        terminals,

        defaultShell: defaultTerminalShell,

        onShellChange: setDefaultTerminalShell,

        onGotoTerminal: handleGotoTerminal,

        onCloseTerminal: handleCloseTerminal,

        onNewTerminal,

      },

    });

  }, [

    terminalsEnabled,

    hasProject,

    terminals,

    defaultTerminalShell,

    setDefaultTerminalShell,

    handleGotoTerminal,

    handleCloseTerminal,

    onNewTerminal,

    setHeaderActions,

  ]);



  useEffect(

    () => () => {

      setHeaderActions({ terminal: null });

    },

    [setHeaderActions],

  );



  return null;

}
