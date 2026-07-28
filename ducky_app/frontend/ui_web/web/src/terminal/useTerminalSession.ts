import { useCallback, useEffect, useRef, useState } from "react";

import { Terminal } from "@xterm/xterm";

import { FitAddon } from "@xterm/addon-fit";

import { WebLinksAddon } from "@xterm/addon-web-links";

import "@xterm/xterm/css/xterm.css";



import { getApi } from "../hooks/usePanelApi";

import { useAppearance } from "../theme/AppearanceContext";

import { readTerminalTheme } from "../theme/readCssVar";

import { resolveMonoFontFamily, resolveMonoFontSize } from "../verse-editor/monaco/resolveMonacoFontFamily";



type WsMessage =

  | { type: "output"; data: string }

  | { type: "exit"; code: number }

  | { type: "status"; alive?: boolean; exit_code?: number | null }

  | { type: "input"; data: string }

  | { type: "resize"; cols: number; rows: number };



const RECONNECT_MS = 400;

const MAX_RECONNECT_ATTEMPTS = 12;



export function formatTerminalExitCode(code: number): string {

  if (code === 130) return "130 (interrupted)";

  if (code === 3221225786 || (code & 0xffffffff) === 0xc000013a) return "130 (shell exited)";

  return String(code);

}



function focusTerminal(term: Terminal) {

  try {

    term.focus();

  } catch {

    // ignore

  }

}



function safeFit(fit: FitAddon, container: HTMLElement | null): void {

  if (!container || container.clientWidth <= 0 || container.clientHeight <= 0) return;

  try {

    fit.fit();

  } catch {

    // ResizeObserver can fire during unmount while the pane is display:none.

  }

}



function safeRefresh(term: Terminal): void {

  if (term.rows < 1) return;

  try {

    term.refresh(0, term.rows - 1);

  } catch {

    // ignore teardown races

  }

}



function safeWrite(term: Terminal, data: string): void {

  try {

    term.write(data);

  } catch {

    // ignore writes during dispose

  }

}



export function useTerminalSession(sessionId: string, wsUrl: string, visible: boolean) {

  const { cssVars } = useAppearance();

  const containerRef = useRef<HTMLDivElement>(null);

  const termRef = useRef<Terminal | null>(null);

  const fitRef = useRef<FitAddon | null>(null);

  const wsRef = useRef<WebSocket | null>(null);

  const unmountedRef = useRef(false);

  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const reconnectAttemptsRef = useRef(0);

  const shellAliveRef = useRef(true);

  const visibleRef = useRef(visible);

  const [connected, setConnected] = useState(false);

  const [shellAlive, setShellAlive] = useState(true);

  const [exitCode, setExitCode] = useState<number | null>(null);



  const setConnectedSafe = useCallback((value: boolean) => {

    if (!unmountedRef.current) setConnected(value);

  }, []);



  const setShellAliveSafe = useCallback((value: boolean) => {

    if (!unmountedRef.current) setShellAlive(value);

  }, []);



  const setExitCodeSafe = useCallback((value: number | null) => {

    if (!unmountedRef.current) setExitCode(value);

  }, []);



  useEffect(() => {

    shellAliveRef.current = shellAlive;

  }, [shellAlive]);



  useEffect(() => {

    visibleRef.current = visible;

  }, [visible]);



  const sendResize = useCallback(() => {

    const term = termRef.current;

    const ws = wsRef.current;

    if (!term || !ws || ws.readyState !== WebSocket.OPEN || unmountedRef.current) return;

    const cols = term.cols;

    const rows = term.rows;

    if (cols < 1 || rows < 1) return;

    ws.send(JSON.stringify({ type: "resize", cols, rows } satisfies WsMessage));

    void getApi()?.terminal_resize(sessionId, cols, rows);

  }, [sessionId]);



  // Reads visibleRef so its identity never changes — the mount effect below must

  // NOT re-run on visibility flips, or every tab switch destroys the terminal,

  // reconnects the WebSocket, and replays the whole scrollback.

  const focusIfVisible = useCallback(() => {

    if (!visibleRef.current || unmountedRef.current) return;

    const term = termRef.current;

    if (term) focusTerminal(term);

  }, []);



  useEffect(() => {

    const container = containerRef.current;

    if (!container || !wsUrl) return;



    unmountedRef.current = false;

    reconnectAttemptsRef.current = 0;

    setShellAliveSafe(true);

    setExitCodeSafe(null);

    shellAliveRef.current = true;



    const term = new Terminal({

      cursorBlink: true,

      convertEol: true,

      fontFamily: resolveMonoFontFamily(cssVars),

      fontSize: resolveMonoFontSize(cssVars),

      theme: readTerminalTheme(),

      scrollback: 5000,

    });

    const fit = new FitAddon();

    term.loadAddon(fit);

    term.loadAddon(new WebLinksAddon());

    term.open(container);

    safeFit(fit, container);

    termRef.current = term;

    fitRef.current = fit;



    const fitAndResize = () => {

      if (unmountedRef.current || !visibleRef.current) return;

      safeFit(fit, container);

      sendResize();

    };



    const onMouseDown = () => focusTerminal(term);

    container.addEventListener("mousedown", onMouseDown);



    const clearReconnectTimer = () => {

      if (reconnectTimerRef.current) {

        clearTimeout(reconnectTimerRef.current);

        reconnectTimerRef.current = null;

      }

    };



    const connect = () => {

      if (unmountedRef.current) return;

      clearReconnectTimer();

      const prev = wsRef.current;

      if (prev && (prev.readyState === WebSocket.OPEN || prev.readyState === WebSocket.CONNECTING)) {

        return;

      }

      if (prev) {

        try {

          prev.close();

        } catch {

          // ignore

        }

      }



      const ws = new WebSocket(wsUrl);

      wsRef.current = ws;



      ws.onopen = () => {

        if (unmountedRef.current) {

          ws.close();

          return;

        }

        reconnectAttemptsRef.current = 0;

        setConnectedSafe(true);

        setShellAliveSafe(true);

        shellAliveRef.current = true;

        fitAndResize();

        window.requestAnimationFrame(() => focusIfVisible());

      };



      ws.onmessage = (ev) => {

        if (unmountedRef.current) return;

        try {

          const msg = JSON.parse(String(ev.data)) as WsMessage;

          if (msg.type === "output") {

            safeWrite(term, msg.data);

          } else if (msg.type === "status") {

            if (typeof msg.alive === "boolean") {

              setShellAliveSafe(msg.alive);

              shellAliveRef.current = msg.alive;

            }

            if (msg.exit_code != null) setExitCodeSafe(msg.exit_code);

          } else if (msg.type === "exit") {

            setShellAliveSafe(false);

            shellAliveRef.current = false;

            setExitCodeSafe(msg.code);

            safeWrite(term, `\r\n\x1b[31m[shell exited: ${formatTerminalExitCode(msg.code)}]\x1b[0m\r\n`);

          }

        } catch {

          if (!unmountedRef.current) safeWrite(term, String(ev.data));

        }

      };



      ws.onclose = () => {

        if (unmountedRef.current) return;

        setConnectedSafe(false);

        if (reconnectAttemptsRef.current >= MAX_RECONNECT_ATTEMPTS) return;

        reconnectAttemptsRef.current += 1;

        reconnectTimerRef.current = setTimeout(connect, RECONNECT_MS);

      };



      ws.onerror = () => {

        if (!unmountedRef.current) setConnectedSafe(false);

      };

    };



    connect();



    const onData = term.onData((data) => {

      const ws = wsRef.current;

      if (!shellAliveRef.current || unmountedRef.current) return;

      if (ws?.readyState === WebSocket.OPEN) {

        ws.send(JSON.stringify({ type: "input", data } satisfies WsMessage));

      }

    });



    const ro = new ResizeObserver(() => {

      fitAndResize();

    });

    ro.observe(container);



    return () => {

      unmountedRef.current = true;

      reconnectAttemptsRef.current = MAX_RECONNECT_ATTEMPTS;

      clearReconnectTimer();

      container.removeEventListener("mousedown", onMouseDown);

      onData.dispose();

      ro.disconnect();

      const ws = wsRef.current;

      wsRef.current = null;

      if (ws) {

        try {

          ws.close();

        } catch {

          // ignore

        }

      }

      const termToDispose = term;

      termRef.current = null;

      fitRef.current = null;

      window.requestAnimationFrame(() => {

        try {

          termToDispose.dispose();

        } catch {

          // ignore

        }

      });

    };

  }, [sessionId, wsUrl, sendResize, focusIfVisible, setConnectedSafe, setShellAliveSafe, setExitCodeSafe]);



  useEffect(() => {

    const term = termRef.current;

    if (!term || unmountedRef.current || !visibleRef.current) return;

    term.options.theme = readTerminalTheme();

    term.options.fontFamily = resolveMonoFontFamily(cssVars);

    term.options.fontSize = resolveMonoFontSize(cssVars);

    safeRefresh(term);

  }, [cssVars]);



  useEffect(() => {

    if (!visible) return;

    const fit = fitRef.current;

    const container = containerRef.current;

    if (!fit || !container) return;

    const id = window.requestAnimationFrame(() => {

      if (unmountedRef.current) return;

      safeFit(fit, container);

      sendResize();

      focusIfVisible();

    });

    return () => window.cancelAnimationFrame(id);

  }, [visible, sendResize, focusIfVisible]);



  return { containerRef, connected, shellAlive, exitCode };

}


