import type { AgentEvent } from "../../types/panel";

/**
 * The single definition of what an agent event implies about a run's liveness.
 *
 * Shared by the global running-set ({@link useRunningAgents}, which drives the
 * sidebar / tab "running" dots and the isAgentRunning prop) and mirrored by the
 * per-chat reducer's status transitions. Keeping one definition means the global
 * indicator and the per-chat Stop button can never disagree about whether a chat
 * is running.
 *
 * "start" covers every event that proves the worker is producing output —
 * including `thinking`, so a run that is only reasoning (no visible tokens yet)
 * still counts as running instead of looking idle.
 */
export function agentEventRunningSignal(event: AgentEvent): "start" | "stop" | null {
  switch (event.type) {
    case "text_delta":
    case "thinking":
    case "tool":
      return "start";
    case "agent_stopped":
    case "error":
      return "stop";
    default:
      return null;
  }
}
