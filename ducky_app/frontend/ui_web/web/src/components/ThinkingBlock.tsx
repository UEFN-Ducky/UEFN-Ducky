import { useEffect, useRef } from "react";
import { Icons } from "../icons/Icons";
import {
  chatCollapseKey,
  readChatCollapseFlag,
  useChatCollapseScope,
  useChatCollapseState,
  writeChatCollapseFlag,
} from "../hooks/useChatCollapseState";
import { InlineStopButton } from "./InlineStopButton";

interface ThinkingBlockProps {
  text: string;
  /** True while reasoning is still streaming in for this turn. */
  isStreaming?: boolean;
  /** True when the turn crashed/stalled mid-thought — keep the reasoning shown. */
  interrupted?: boolean;
  /** Stop the live run (visible on the collapsed header while thinking). */
  onStop?: () => void;
}

/**
 * Cursor-style collapsible reasoning panel. Auto-expands while the model is
 * thinking, collapses to a one-line summary once the answer starts / finishes.
 * The raw reasoning is preserved so it can be re-expanded later.
 */
export function ThinkingBlock({ text, isStreaming, interrupted, onStop }: ThinkingBlockProps) {
  const collapseScope = useChatCollapseScope();
  const openKey = chatCollapseKey(collapseScope, "thinking");
  const userToggledKey = chatCollapseKey(collapseScope, "thinking-user");
  const defaultOpen = !!isStreaming || !!interrupted;
  const [open, setOpen] = useChatCollapseState(openKey, defaultOpen);
  const userToggled = useRef(readChatCollapseFlag(userToggledKey));
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (userToggled.current) return;
    setOpen(!!isStreaming || !!interrupted);
  }, [isStreaming, interrupted, setOpen]);

  // Keep the newest reasoning in view while streaming and expanded.
  useEffect(() => {
    if (open && isStreaming && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [text, open, isStreaming]);

  const trimmed = text.trim();
  if (!trimmed) return null;

  return (
    <div className={`thinking-block${isStreaming ? " thinking-block--streaming" : ""}`}>
      <div className="thinking-block-header-row">
        <button
          type="button"
          className="thinking-block-header"
          onClick={() => {
            userToggled.current = true;
            writeChatCollapseFlag(userToggledKey, true);
            setOpen((v) => !v);
          }}
          aria-expanded={open}
        >
          <span className={`thinking-block-caret${open ? " is-open" : ""}`}>
            <Icons.ChevronDown />
          </span>
          <span className="thinking-block-label">
            {isStreaming ? "Thinking…" : interrupted ? "Thought process (interrupted)" : "Thought process"}
          </span>
          {isStreaming ? <span className="thinking-block-pulse" aria-hidden="true" /> : null}
        </button>
        {isStreaming && onStop ? <InlineStopButton onClick={onStop} /> : null}
      </div>
      {open ? (
        <div ref={bodyRef} className="thinking-block-body">
          {trimmed}
        </div>
      ) : null}
    </div>
  );
}
