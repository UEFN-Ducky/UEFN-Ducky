import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { ScanProgressState } from "../verse-editor/lsp/fileDiagnosticRegistry";
import type { TerminalShell } from "../terminal/types";

export interface HeaderSaveAction {
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
}

export interface HeaderVerseWorkflowAction {
  connected: boolean;
  buildState: number;
  canPush: boolean;
  busy: boolean;
  buildIconSrc: string;
  onConnect: () => void;
  onDisconnect: () => void;
  onCompile: () => void;
  onPush: () => void;
  lastLog?: string;
}

export type ProblemsStatus = "scanning" | "ok" | "warning" | "error";

export interface HeaderProblemsAction {
  status: ProblemsStatus;
  errorCount: number;
  warningCount: number;
  files: Array<{
    path: string;
    errors: number;
    warnings: number;
    items: Array<{
      line: number;
      column: number;
      message: string;
      severity: "error" | "warning";
    }>;
  }>;
  scanProgress: ScanProgressState | null;
  fullScanActive: boolean;
  scanInProgress: boolean;
  fileCheckInProgress: boolean;
  onNavigate: (path: string, line: number, column: number) => void;
  onRefresh: () => void;
  /** Re-check only the active editor file (null when no .verse file is active). */
  onRefreshActiveFile: (() => void) | null;
  /** Base name of the active .verse file, for the recheck button tooltip. */
  activeFileName: string | null;
  /** Stop the in-flight project scan (shown while a scan is running). */
  onStopScan: () => void;
}

export interface HeaderTerminalItem {
  id: string;
  sessionId: string;
  name: string;
  shell: string;
  active: boolean;
  /** True when the tab is closed but the shell is still alive (reopen from header). */
  parked?: boolean;
  running: boolean;
  runner: "mcp" | "user" | null;
}

export interface HeaderTerminalAction {
  terminals: HeaderTerminalItem[];
  defaultShell: TerminalShell;
  onShellChange: (shell: TerminalShell) => void;
  onGotoTerminal: (tabId: string) => void;
  onCloseTerminal: (tabId: string) => void;
  onNewTerminal: () => void;
}

interface AppHeaderActionsState {
  save: HeaderSaveAction | null;
  verseWorkflow: HeaderVerseWorkflowAction | null;
  problems: HeaderProblemsAction | null;
  terminal: HeaderTerminalAction | null;
}

interface AppHeaderActionsContextValue {
  actions: AppHeaderActionsState;
  setHeaderActions: (actions: Partial<AppHeaderActionsState>) => void;
  clearHeaderActions: () => void;
  problemsMenuOpen: boolean;
  setProblemsMenuOpen: (open: boolean) => void;
}

const EMPTY: AppHeaderActionsState = {
  save: null,
  verseWorkflow: null,
  problems: null,
  terminal: null,
};

const AppHeaderActionsContext = createContext<AppHeaderActionsContextValue | null>(null);

export function AppHeaderActionsProvider({ children }: { children: ReactNode }) {
  const [actions, setActions] = useState<AppHeaderActionsState>(EMPTY);
  const [problemsMenuOpen, setProblemsMenuOpen] = useState(false);

  const setHeaderActions = useCallback((patch: Partial<AppHeaderActionsState>) => {
    setActions((prev) => ({ ...prev, ...patch }));
  }, []);

  const clearHeaderActions = useCallback(() => {
    setActions(EMPTY);
  }, []);

  const value = useMemo(
    () => ({ actions, setHeaderActions, clearHeaderActions, problemsMenuOpen, setProblemsMenuOpen }),
    [actions, setHeaderActions, clearHeaderActions, problemsMenuOpen],
  );

  return <AppHeaderActionsContext.Provider value={value}>{children}</AppHeaderActionsContext.Provider>;
}

export function useAppHeaderActions(): AppHeaderActionsState {
  const ctx = useContext(AppHeaderActionsContext);
  return ctx?.actions ?? EMPTY;
}

export function useRegisterAppHeaderActions(): {
  setHeaderActions: (actions: Partial<AppHeaderActionsState>) => void;
  clearHeaderActions: () => void;
} {
  const ctx = useContext(AppHeaderActionsContext);
  if (!ctx) {
    return { setHeaderActions: () => {}, clearHeaderActions: () => {} };
  }
  return { setHeaderActions: ctx.setHeaderActions, clearHeaderActions: ctx.clearHeaderActions };
}

export function useProblemsMenuOpen(): {
  problemsMenuOpen: boolean;
  setProblemsMenuOpen: (open: boolean) => void;
} {
  const ctx = useContext(AppHeaderActionsContext);
  return {
    problemsMenuOpen: ctx?.problemsMenuOpen ?? false,
    setProblemsMenuOpen: ctx?.setProblemsMenuOpen ?? (() => {}),
  };
}
