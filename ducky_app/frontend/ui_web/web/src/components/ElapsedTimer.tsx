import { memo } from "react";
import { useChatTurnTimer } from "../hooks/useChatTurnTimer";

export type ElapsedTimerProps = {
  chatId: string;
  /**
   * `live` — only while the turn is running.
   * `idle` — only the finished duration (hidden while running).
   * `always` — running or last finished duration (default).
   */
  when?: "live" | "idle" | "always";
  /** Prefix when the turn is finished, e.g. "Took". */
  idlePrefix?: string;
  className?: string;
  title?: string;
};

/**
 * Shared turn-duration clock. Same component for hover cards, chat footer, etc.
 */
export const ElapsedTimer = memo(function ElapsedTimer({
  chatId,
  when = "always",
  idlePrefix,
  className = "",
  title,
}: ElapsedTimerProps) {
  const { ms, running, label } = useChatTurnTimer(chatId);
  if (label == null || ms == null) return null;
  if (when === "live" && !running) return null;
  if (when === "idle" && running) return null;

  const text = !running && idlePrefix ? `${idlePrefix} ${label}` : label;
  const classes = ["elapsed-timer", running ? "elapsed-timer--live" : "elapsed-timer--done", className]
    .filter(Boolean)
    .join(" ");

  return (
    <span
      className={classes}
      title={title ?? (running ? `Working for ${label}` : `Last turn took ${label}`)}
      aria-label={running ? `Working for ${label}` : `Last turn took ${label}`}
    >
      {text}
    </span>
  );
});
