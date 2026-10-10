export type TerminalShell = "bash" | "powershell";

export interface TerminalSessionDto {
  session_id: string;
  shell: TerminalShell;
  cwd: string;
  title: string;
  ws_url: string;
  busy?: boolean;
  alive?: boolean;
  tab_id?: string;
}

export interface PendingTerminalCommand {
  request_id: string;
  session_id: string;
  command: string;
  shell?: string;
  cwd?: string;
  conv_id?: string;
  source?: string;
  /** What "Always allow … in this chat" names; empty = not offered (pushes, deletes, chains). */
  rule_label?: string;
  /** Pushes or publishes a local-only AI plugin: no "Allow everything". */
  local_only?: boolean;
  /** Unix seconds when the agent asked. */
  created_at?: number;
}

/** once / always (this command in this chat) / all (everything in this chat) / deny. */
export type TerminalApprovalChoice = "once" | "always" | "all" | "deny";

export interface TerminalSpawnResult {
  ok: boolean;
  session_id?: string;
  shell?: TerminalShell;
  cwd?: string;
  title?: string;
  ws_url?: string;
  tab_id?: string;
  shell_fallback?: boolean;
  error?: string;
}

export const DEFAULT_TERMINAL_SHELL: TerminalShell =
  typeof navigator !== "undefined" && /Windows/i.test(navigator.userAgent) ? "powershell" : "bash";

export function terminalTabLabel(shell: TerminalShell, title?: string): string {
  const base = title?.trim() || shell;
  return base.length > 24 ? `${base.slice(0, 22)}…` : base;
}
