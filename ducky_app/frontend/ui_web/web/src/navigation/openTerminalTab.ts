/**
 * Open a terminal session as a tab (the workflow card's Open terminal, anything that only
 * knows the session id). Same singleton pattern as the Workflows tab: ChatView registers.
 */

let openTerminalFn: ((sessionId: string) => void) | null = null;

export function registerOpenTerminalTab(fn: (sessionId: string) => void): () => void {
  openTerminalFn = fn;
  return () => {
    if (openTerminalFn === fn) openTerminalFn = null;
  };
}

export function requestOpenTerminalTab(sessionId: string): void {
  if (sessionId) openTerminalFn?.(sessionId);
}
