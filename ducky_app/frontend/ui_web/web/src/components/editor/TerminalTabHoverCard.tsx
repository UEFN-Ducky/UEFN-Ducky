import type { ReactNode } from "react";

import { Icons } from "../../icons/Icons";
import type { EditorTab } from "../../types/panel";
import type { TerminalBusyStatus } from "../../terminal/useTerminalBusyStatuses";
import { EditorTabHoverCardShell } from "./EditorTabHoverCardShell";

const SHELL_LABELS: Record<string, string> = {
  bash: "Bash",
  powershell: "PowerShell",
};

interface TerminalTabHoverCardProps {
  tab: EditorTab;
  busyStatus?: TerminalBusyStatus | null;
  disabled?: boolean;
  children: ReactNode;
}

export function TerminalTabHoverCard({
  tab,
  busyStatus,
  disabled = false,
  children,
}: TerminalTabHoverCardProps) {
  const shell = tab.terminalShell ?? "bash";
  const shellLabel = SHELL_LABELS[shell] ?? shell;
  const cwd = tab.terminalCwd?.replace(/\\/g, "/") ?? "";

  return (
    <EditorTabHoverCardShell
      disabled={disabled}
      card={
        <>
          <div className="editor-tab-hover-card-header">
            <div className="editor-tab-hover-card-icon editor-tab-hover-card-icon--terminal">
              <Icons.Terminal />
            </div>
            <div className="editor-tab-hover-card-titles">
              <div className="editor-tab-hover-card-name">{tab.name}</div>
              <div className="editor-tab-hover-card-subtitle">{shellLabel}</div>
            </div>
          </div>
          {cwd ? (
            <div className="editor-tab-hover-card-path" title={cwd}>
              {cwd}
            </div>
          ) : null}
          {busyStatus?.running ? (
            <div className="editor-tab-hover-card-status editor-tab-hover-card-status--running">
              <span className="sidebar-agent-spinner" aria-hidden="true" />
              {busyStatus.runner === "mcp" ? "Agent command running" : "Running command"}
            </div>
          ) : null}
        </>
      }
    >
      {children}
    </EditorTabHoverCardShell>
  );
}
