import { DuckyAvatar, DUCKY_AVATAR_SIZES } from "./ducky/DuckyAvatars";
import { Icons } from "../icons/Icons";
import type { AgentMode } from "../types/panel";

interface ChatPaneEmptyStateProps {
  hasApiKey: boolean;
  selectedModel: string;
  /** External coding agents pin their own model — never nag to choose one. */
  modelManagedByAgent?: boolean;
  modelsLoading: boolean;
  noModelsAvailable: boolean;
  agentMode: AgentMode;
  duckyStyle?: string;
  /** Group roundtable — no per-tab model picker. */
  isGroup?: boolean;
  /** Focus window empty pane: padding around the mascot is a caption drag surface. */
  allowWindowDrag?: boolean;
}
const MODE_HINTS: Record<AgentMode, string> = {
  agent: "Agent mode can run tools, edit files, and control UEFN.",
  plan: "Plan mode helps you design an approach before making changes.",
  ask: "Ask mode answers questions without running tools.",
};

export function ChatPaneEmptyState({
  hasApiKey,
  selectedModel,
  modelManagedByAgent = false,
  modelsLoading,
  noModelsAvailable,
  agentMode,
  duckyStyle,
  isGroup = false,
  allowWindowDrag = false,
}: ChatPaneEmptyStateProps) {
  let headline = isGroup ? "Group chat" : "New ducky";
  let message: string;

  if (modelsLoading && !isGroup) {
    headline = "Loading models";
    message = "Fetching available models…";
  } else if (noModelsAvailable && !isGroup) {
    headline = "No models available";
    message = hasApiKey
      ? "No models were found for your configured providers. Open Settings → LLMs to verify API keys, or install a gateway from Store → Gateways."
      : "Add an API key in Settings → LLMs before you can chat.";
  } else if (!hasApiKey && !isGroup) {
    headline = "API key required";
    message = "Open Settings → LLMs and add an API key before you can send messages.";
  } else if (isGroup) {
    message = "Invite duckies above (or nest a group folder inside), then type a message to start the roundtable.";
  } else if (!selectedModel && !modelManagedByAgent) {
    headline = "Choose a model";
    message = "Select a model from the menu below, then type your first message.";
  } else {
    message = `Type a message below to get started. ${MODE_HINTS[agentMode]}`;
  }

  return (
    <div
      className={`chat-pane-empty-state-root${allowWindowDrag ? " drag-region app-drag-surface" : ""}`}
    >
      <div className={allowWindowDrag ? "no-drag" : undefined}>
        <div className="chat-pane-empty-state-avatar-wrap">
          {isGroup ? (
            <span className="chat-pane-empty-state-group-icon" aria-hidden>
              <Icons.Users />
            </span>
          ) : (
            <DuckyAvatar styleId={duckyStyle} size={DUCKY_AVATAR_SIZES.emptyPane} />
          )}
        </div>
        <h2 className="chat-pane-empty-state-title">{headline}</h2>
        <p className="chat-pane-empty-state-message">{message}</p>
        {hasApiKey &&
        (isGroup || ((selectedModel || modelManagedByAgent) && !modelsLoading && !noModelsAvailable)) ? (
          <p className="chat-pane-empty-state-hint">
            <Icons.Send />
            Press Enter to send · Shift+Enter for a new line
          </p>
        ) : null}
      </div>
    </div>
  );
}
