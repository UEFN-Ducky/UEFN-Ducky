import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRegisterAppHeaderActions } from "../contexts/AppHeaderActionsContext";
import { subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { useListenerStatus } from "../hooks/useListenerStatus";
import { getApi } from "../hooks/usePanelApi";
import type { VerseWorkflowStatusDto } from "../types/panel";
import { fileDiagnosticRegistry } from "../verse-editor/lsp/fileDiagnosticRegistry";
import { emitAppHook } from "../sfx/appHooks";

const BUILD_STATE = {
  Success: 0,
  Warnings: 1,
  Errors: 2,
  Building: 3,
  NoBuild: 4,
} as const;

function buildIconSrc(buildState: number): string {
  switch (buildState) {
    case BUILD_STATE.Building:
      return "/verse-workflow/verse-icon-loading.svg";
    case BUILD_STATE.Errors:
      return "/verse-workflow/verse-icon-error.svg";
    case BUILD_STATE.Warnings:
      return "/verse-workflow/verse-icon-warning.svg";
    case BUILD_STATE.Success:
      return "/verse-workflow/verse-icon-success.svg";
    default:
      return "/verse-workflow/verse-icon-build.svg";
  }
}

/** UEFN workflow build_state can stay Errors after LSP diagnostics are fixed — mirror live diagnostics when idle. */
function effectiveBuildState(workflowState: number, busy: boolean): number {
  if (workflowState === BUILD_STATE.Building || busy) {
    return BUILD_STATE.Building;
  }

  const { errors, warnings } = fileDiagnosticRegistry.getTotals();
  if (errors > 0) return BUILD_STATE.Errors;
  if (warnings > 0) return BUILD_STATE.Warnings;

  if (workflowState === BUILD_STATE.Errors || workflowState === BUILD_STATE.Warnings) {
    return BUILD_STATE.Success;
  }

  return workflowState;
}

interface VerseWorkflowBridgeProps {
  enabled: boolean;
}

export function VerseWorkflowBridge({ enabled }: VerseWorkflowBridgeProps) {
  const { setHeaderActions } = useRegisterAppHeaderActions();
  const listener = useListenerStatus(8000);
  const active = enabled && listener.online;
  const [status, setStatus] = useState<VerseWorkflowStatusDto>({
    connected: false,
    build_state: BUILD_STATE.NoBuild,
    can_push_verse_changes: false,
    last_log: "",
  });
  const [busy, setBusy] = useState(false);
  const [diagRev, setDiagRev] = useState(0);

  useEffect(() => fileDiagnosticRegistry.subscribe(() => setDiagRev((n) => n + 1)), []);

  const displayBuildState = useMemo(() => {
    void diagRev;
    return effectiveBuildState(status.build_state, busy);
  }, [status.build_state, busy, diagRev]);

  const prevBuildStateRef = useRef(displayBuildState);
  useEffect(() => {
    const prev = prevBuildStateRef.current;
    prevBuildStateRef.current = displayBuildState;
    if (prev !== BUILD_STATE.Errors && displayBuildState === BUILD_STATE.Errors) {
      emitAppHook("verse.errors");
    }
  }, [displayBuildState]);

  const refreshStatus = useCallback(async () => {
    const api = getApi();
    if (!api) return;
    try {
      const next = await api.get_verse_workflow_status();
      setStatus(next);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    void refreshStatus();
    const unsub = subscribeAgentEvents((event) => {
      if (event.type === "verse_workflow_state") {
        setStatus({
          connected: Boolean(event.connected),
          build_state: Number(event.build_state ?? BUILD_STATE.NoBuild),
          can_push_verse_changes: Boolean(event.can_push_verse_changes),
          last_log: String(event.last_log ?? ""),
        });
      }
    });
    return unsub;
  }, [active, refreshStatus]);

  useEffect(() => {
    if (!active) {
      setHeaderActions({ verseWorkflow: null });
      return;
    }
    const api = getApi();
    if (!api) return;
    void api.connect_verse_workflow().then(setStatus).catch(() => {});
    return () => {
      void api.disconnect_verse_workflow();
    };
  }, [active, setHeaderActions]);

  const connect = useCallback(async () => {
    const api = getApi();
    if (!api || busy) return;
    setBusy(true);
    try {
      setStatus(await api.connect_verse_workflow());
    } finally {
      setBusy(false);
    }
  }, [busy]);

  const compile = useCallback(async () => {
    const api = getApi();
    if (!api || busy || !status.connected) return;
    setBusy(true);
    try {
      const { runBridgeJob } = await import("../hooks/bridgeJobAsync");
      await runBridgeJob("compile_verse_project", [], 180_000);
      await refreshStatus();
    } finally {
      setBusy(false);
    }
  }, [busy, refreshStatus, status.connected]);

  const push = useCallback(async () => {
    const api = getApi();
    if (!api || busy || !status.can_push_verse_changes) return;
    setBusy(true);
    try {
      await api.push_verse_changes(true);
      await refreshStatus();
    } finally {
      setBusy(false);
    }
  }, [busy, refreshStatus, status.can_push_verse_changes]);

  useEffect(() => {
    if (!active) return;
    setHeaderActions({
      verseWorkflow: {
        connected: status.connected,
        buildState: displayBuildState,
        canPush: status.can_push_verse_changes,
        busy,
        buildIconSrc: buildIconSrc(displayBuildState),
        onConnect: () => void connect(),
        onDisconnect: () => {
          const api = getApi();
          if (api) void api.disconnect_verse_workflow().then(setStatus);
        },
        onCompile: () => void compile(),
        onPush: () => void push(),
        lastLog: status.last_log,
      },
    });
    return () => setHeaderActions({ verseWorkflow: null });
  }, [active, status, displayBuildState, busy, connect, compile, push, setHeaderActions]);

  return null;
}
