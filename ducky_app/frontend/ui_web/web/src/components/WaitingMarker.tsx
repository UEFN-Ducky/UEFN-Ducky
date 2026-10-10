import type { MouseEvent, PointerEvent } from "react";

import { openWaitingChat } from "../hooks/waitingChats";

export const WAITING_LABEL = "Waiting for your answer";

type Props = {
  /** The chat this marker sits on (opened and scrolled to its card on click). */
  convId: string;
  name?: string;
  variant: "tab" | "sidebar" | "folder" | "chip";
  /** Inside another button (a group member chip): a plain badge. */
  interactive?: boolean;
};

const stop = (event: PointerEvent | MouseEvent) => event.stopPropagation();

/** Shown instead of the running spinner while the chat waits on a question or command card. */
export function WaitingMarker({ convId, name = "", variant, interactive = true }: Props) {
  const className = `chat-waiting-marker chat-waiting-marker--${variant}`;
  if (!interactive) {
    return (
      <span className={className} role="img" aria-label={WAITING_LABEL} title={WAITING_LABEL} data-waiting-marker={convId}>
        ?
      </span>
    );
  }
  return (
    <button
      type="button"
      className={`no-drag ${className}`}
      title={`${WAITING_LABEL}. Click to answer it.`}
      aria-label={WAITING_LABEL}
      data-waiting-marker={convId}
      draggable={false}
      onPointerDown={stop}
      onMouseDown={stop}
      onDoubleClick={stop}
      onClick={(event) => {
        // On a tab the click goes on to the tab too, so it activates in any window
        // (a pop-out window has no chat opener of its own).
        if (variant !== "tab") event.stopPropagation();
        event.preventDefault();
        openWaitingChat(convId, name);
      }}
    >
      ?
    </button>
  );
}
