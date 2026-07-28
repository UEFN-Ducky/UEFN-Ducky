import { useTerminalSession } from "./useTerminalSession";

interface TerminalPaneProps {
  sessionId: string;
  wsUrl: string;
  visible: boolean;
  variant?: "default" | "focus";
}

export function TerminalPane({
  sessionId,
  wsUrl,
  visible,
  variant = "default",
}: TerminalPaneProps) {
  const { containerRef } = useTerminalSession(sessionId, wsUrl, visible);

  return (
    <div
      className={`terminal-pane terminal-pane--${variant}${visible ? "" : " terminal-pane--hidden"}`}
      data-no-translate
    >
      <div ref={containerRef} className="terminal-pane-view no-drag" tabIndex={-1} />
    </div>
  );
}
