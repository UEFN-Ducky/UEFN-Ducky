import type { LinkedAgent, LinkedAgentStatus } from "../types/panel";
import { ChatShortcutCard, type ChatShortcutStatusTone } from "./ChatShortcutCard";

interface LinkedAgentCardProps {
  agent: LinkedAgent;
  duckyStyle?: string;
  onOpen: () => void;
  onStop: () => void;
  compact?: boolean;
}

function statusLabel(status: LinkedAgentStatus): string {
  switch (status) {
    case "running":
      return "Running";
    case "done":
      return "Done";
    case "error":
      return "Error";
    case "timeout":
      return "Timeout";
    case "cancelled":
      return "Stopped";
    default:
      return status;
  }
}

function statusTone(status: LinkedAgentStatus): ChatShortcutStatusTone {
  return status;
}

export function LinkedAgentCard({ agent, duckyStyle, onOpen, onStop, compact = false }: LinkedAgentCardProps) {
  const isRunning = agent.status === "running";

  return (
    <ChatShortcutCard
      compact={compact}
      title={agent.title}
      duckyStyle={duckyStyle}
      statusLabel={statusLabel(agent.status)}
      statusTone={statusTone(agent.status)}
      isRunning={isRunning}
      onOpen={onOpen}
      onStop={isRunning ? onStop : undefined}
    />
  );
}
