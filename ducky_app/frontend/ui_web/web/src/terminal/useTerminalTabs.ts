import { useCallback, useEffect, useRef, useState } from "react";
import { subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { getApi } from "../hooks/usePanelApi";
import { getTerminalsEnabled } from "../contexts/TerminalsSettingsContext";
import type { AgentEvent, EditorTab } from "../types/panel";
import { terminalTabId } from "../types/panel";
import {
  DEFAULT_TERMINAL_SHELL,
  terminalTabLabel,
  type TerminalSessionDto,
  type TerminalShell,
  type TerminalSpawnResult,
} from "./types";
import type { EditorLayoutApi } from "../hooks/useEditorLayout";

function dtoToTab(dto: TerminalSessionDto): EditorTab {
  return {
    id: terminalTabId(dto.session_id),
    kind: "terminal",
    name: terminalTabLabel(dto.shell, dto.title),
    terminalSessionId: dto.session_id,
    terminalShell: dto.shell,
    terminalWsUrl: dto.ws_url,
    terminalCwd: dto.cwd,
  };
}

function sessionRecordToDto(session: Record<string, unknown>): TerminalSessionDto | null {
  const sessionId = String(session.session_id || "").trim();
  const wsUrl = String(session.ws_url || "").trim();
  if (!sessionId || !wsUrl) return null;
  const shell = (String(session.shell || DEFAULT_TERMINAL_SHELL) as TerminalShell) || DEFAULT_TERMINAL_SHELL;
  return {
    session_id: sessionId,
    shell,
    cwd: String(session.cwd || ""),
    title: String(session.title || shell),
    ws_url: wsUrl,
  };
}

export function useTerminalTabs(
  editor: Pick<EditorLayoutApi, "openTab" | "closeTabInLayout" | "openTabsRef" | "remapTabId" | "setOpenTabs">,
) {
  const sessionsRef = useRef<Map<string, TerminalSessionDto>>(new Map());
  const [parkedTabs, setParkedTabs] = useState<EditorTab[]>([]);
  const [, bump] = useState(0);

  const rememberSession = useCallback((dto: TerminalSessionDto) => {
    sessionsRef.current.set(dto.session_id, dto);
    bump((n) => n + 1);
  }, []);

  const unparkSession = useCallback((sessionId: string) => {
    setParkedTabs((prev) => prev.filter((t) => t.terminalSessionId !== sessionId));
  }, []);

  const parkTab = useCallback((tab: EditorTab) => {
    if (tab.kind !== "terminal" || !tab.terminalSessionId || !tab.terminalWsUrl) return;
    const dto: TerminalSessionDto = {
      session_id: tab.terminalSessionId,
      shell: tab.terminalShell ?? DEFAULT_TERMINAL_SHELL,
      cwd: tab.terminalCwd || "",
      title: tab.name,
      ws_url: tab.terminalWsUrl,
    };
    rememberSession(dto);
    setParkedTabs((prev) => {
      if (prev.some((t) => t.terminalSessionId === tab.terminalSessionId)) return prev;
      return [...prev, dtoToTab(dto)];
    });
  }, [rememberSession]);

  const openTerminalTab = useCallback(
    (dto: TerminalSessionDto, options?: { activate?: boolean }) => {
      rememberSession(dto);
      unparkSession(dto.session_id);
      editor.openTab(dtoToTab(dto), options);
    },
    [rememberSession, unparkSession, editor],
  );

  const spawnAndOpen = useCallback(
    async (shell: TerminalShell = DEFAULT_TERMINAL_SHELL, title?: string) => {
      if (!getTerminalsEnabled()) return null;
      const api = getApi();
      if (!api) return null;
      const result = (await api.terminal_spawn(shell, "", title || shell)) as unknown as TerminalSpawnResult;
      if (!result.ok || !result.session_id || !result.ws_url) {
        console.warn("[terminal] spawn failed:", result.error || "unknown error");
        return null;
      }
      const actualShell = (result.shell as TerminalShell) || shell;
      const dto: TerminalSessionDto = {
        session_id: result.session_id,
        shell: actualShell,
        cwd: result.cwd || "",
        title: result.shell_fallback ? `${actualShell} (bash unavailable)` : result.title || title || actualShell,
        ws_url: result.ws_url,
      };
      openTerminalTab(dto);
      return dto;
    },
    [openTerminalTab],
  );

  /** Hide the tab but keep the shell process alive (reopen from the header list). */
  const parkTerminalTab = useCallback(
    (tab: EditorTab) => {
      parkTab(tab);
    },
    [parkTab],
  );

  /** Fully kill the shell and remove it from open + parked lists. */
  const killTerminalTab = useCallback(
    async (tab: EditorTab) => {
      const sessionId = tab.terminalSessionId;
      if (!sessionId) return;
      unparkSession(sessionId);
      sessionsRef.current.delete(sessionId);
      editor.closeTabInLayout(tab.id);
      await getApi()?.terminal_kill(sessionId);
    },
    [unparkSession, editor],
  );

  const reopenTerminalTab = useCallback(
    (tabId: string) => {
      const parked = parkedTabs.find((t) => t.id === tabId);
      if (!parked?.terminalSessionId || !parked.terminalWsUrl) return false;
      const dto =
        sessionsRef.current.get(parked.terminalSessionId) ??
        ({
          session_id: parked.terminalSessionId,
          shell: parked.terminalShell ?? DEFAULT_TERMINAL_SHELL,
          cwd: parked.terminalCwd || "",
          title: parked.name,
          ws_url: parked.terminalWsUrl,
        } satisfies TerminalSessionDto);
      openTerminalTab(dto, { activate: true });
      return true;
    },
    [parkedTabs, openTerminalTab],
  );

  const restartTerminalTab = useCallback(
    async (tab: EditorTab) => {
      const api = getApi();
      if (!api || tab.kind !== "terminal") return null;
      const shell = tab.terminalShell ?? DEFAULT_TERMINAL_SHELL;
      const oldSessionId = tab.terminalSessionId;
      if (oldSessionId) {
        unparkSession(oldSessionId);
        sessionsRef.current.delete(oldSessionId);
        await api.terminal_kill(oldSessionId);
      }
      const result = (await api.terminal_spawn(shell, "", tab.name || shell)) as unknown as TerminalSpawnResult;
      if (!result.ok || !result.session_id || !result.ws_url) {
        console.warn("[terminal] restart failed:", result.error || "unknown error");
        return null;
      }
      const actualShell = (result.shell as TerminalShell) || shell;
      const dto: TerminalSessionDto = {
        session_id: result.session_id,
        shell: actualShell,
        cwd: result.cwd || "",
        title: result.title || tab.name || actualShell,
        ws_url: result.ws_url,
      };
      rememberSession(dto);
      const newTabId = terminalTabId(result.session_id);
      editor.remapTabId(tab.id, newTabId);
      editor.setOpenTabs((prev) =>
        prev.map((t) =>
          t.id === newTabId
            ? {
                ...t,
                name: terminalTabLabel(actualShell, dto.title),
                terminalSessionId: dto.session_id,
                terminalShell: actualShell,
                terminalWsUrl: dto.ws_url,
                terminalCwd: dto.cwd,
              }
            : t,
        ),
      );
      bump((n) => n + 1);
      return dto;
    },
    [rememberSession, unparkSession, editor],
  );

  const getSession = useCallback((sessionId: string) => sessionsRef.current.get(sessionId), []);

  // Recover live sessions that aren't open as tabs (e.g. parked after focus-window close).
  useEffect(() => {
    let cancelled = false;
    const syncParked = async () => {
      if (!getTerminalsEnabled()) return;
      const api = getApi();
      if (!api) return;
      const list = await api.terminal_list().catch(() => null);
      if (cancelled || !list?.sessions) return;
      const openIds = new Set(
        editor.openTabsRef.current
          .filter((t) => t.kind === "terminal" && t.terminalSessionId)
          .map((t) => t.terminalSessionId!),
      );
      const recovered: EditorTab[] = [];
      for (const raw of list.sessions) {
        const dto = sessionRecordToDto(raw as Record<string, unknown>);
        if (!dto || openIds.has(dto.session_id)) continue;
        rememberSession(dto);
        recovered.push(dtoToTab(dto));
      }
      if (recovered.length === 0) return;
      setParkedTabs((prev) => {
        const byId = new Map(prev.map((t) => [t.terminalSessionId!, t]));
        for (const tab of recovered) {
          if (tab.terminalSessionId) byId.set(tab.terminalSessionId, tab);
        }
        // Drop parked entries whose process is gone.
        const liveIds = new Set(
          list.sessions.map((s) => String((s as Record<string, unknown>).session_id || "")).filter(Boolean),
        );
        for (const id of [...byId.keys()]) {
          if (!liveIds.has(id) && !openIds.has(id)) byId.delete(id);
        }
        return [...byId.values()];
      });
    };
    void syncParked();
    const timer = window.setInterval(() => void syncParked(), 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [editor, rememberSession]);

  useEffect(() => {
    return subscribeAgentEvents((event: AgentEvent) => {
      if (event.type === "terminal_open" && event.session_id && event.ws_url) {
        if (!getTerminalsEnabled()) return;
        openTerminalTab({
          session_id: event.session_id,
          shell: (event.shell as TerminalShell) || DEFAULT_TERMINAL_SHELL,
          cwd: event.cwd || "",
          title: event.title || event.shell || "terminal",
          ws_url: event.ws_url,
        });
        return;
      }
      if (event.type === "terminal_close" && event.session_id) {
        const tabId = terminalTabId(event.session_id);
        editor.closeTabInLayout(tabId);
        unparkSession(event.session_id);
        sessionsRef.current.delete(event.session_id);
      }
    });
  }, [openTerminalTab, unparkSession, editor]);

  const openTerminalTabBySession = useCallback(
    (sessionId: string, title: string, wsUrl: string, shell?: string, cwd?: string) => {
      openTerminalTab(
        {
          session_id: sessionId,
          shell: (shell as TerminalShell) || DEFAULT_TERMINAL_SHELL,
          cwd: cwd || "",
          title,
          ws_url: wsUrl,
        },
        { activate: true },
      );
    },
    [openTerminalTab],
  );

  return {
    openTerminalTab,
    openTerminalTabBySession,
    spawnAndOpen,
    parkTerminalTab,
    killTerminalTab,
    reopenTerminalTab,
    parkedTabs,
    restartTerminalTab,
    getSession,
  };
}
