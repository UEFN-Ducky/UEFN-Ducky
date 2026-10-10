import { memo, useLayoutEffect, useRef, useState, type ComponentType } from "react";
import { Icons } from "../icons/Icons";
import { requestOpenChatTab } from "../navigation/openChatReference";
import { codingAgentName, type AutomatedPart, type AutomatedPartKind, type AutomatedPrompt } from "../utils/automatedPrompt";
import { ChatRefText } from "./ChatRefText";
import { InlineStopButton } from "./InlineStopButton";

const PART_ICON: Record<AutomatedPartKind, ComponentType> = {
  message: Icons.Users,
  notice: Icons.AlertTriangle,
  plan: Icons.Plan,
  keeper: Icons.Clock,
};

function AutomatedPartView({ part, expanded }: { part: AutomatedPart; expanded: boolean }) {
  const Icon = PART_ICON[part.kind];
  const agent = codingAgentName(part.agent);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [faded, setFaded] = useState(false);

  useLayoutEffect(() => {
    const el = bodyRef.current;
    setFaded(!expanded && !!el && el.scrollHeight > el.clientHeight + 1);
  }, [part.body, expanded]);

  return (
    <div className={`message-bubble-automated-part is-${part.kind}`}>
      <div className="message-bubble-automated-head">
        <span className="message-bubble-automated-icon" aria-hidden>
          <Icon />
        </span>
        {part.kind === "message" ? <span className="message-bubble-automated-label">From</span> : null}
        {part.fromConvId ? (
          <button
            type="button"
            className="message-bubble-automated-from"
            title={`Open ${part.from}`}
            onClick={(event) => {
              event.stopPropagation();
              requestOpenChatTab(part.fromConvId!, part.from);
            }}
          >
            {part.from}
          </button>
        ) : (
          <span className="message-bubble-automated-from is-static">{part.from}</span>
        )}
        {agent ? <span className="message-bubble-automated-agent">{agent}</span> : null}
        <span className="message-bubble-automated-tag">{part.tag}</span>
      </div>
      {part.body ? (
        <div ref={bodyRef} className={`message-bubble-automated-body${faded ? " is-faded" : ""}`}>
          <ChatRefText text={part.body} />
        </div>
      ) : null}
    </div>
  );
}

/** A turn another agent or Ducky started in this chat: who sent it, never "you", never editable. */
export const AutomatedPromptMessage = memo(function AutomatedPromptMessage({
  prompt,
  onStop,
}: {
  prompt: AutomatedPrompt;
  /** Stop the live run this message started (the latest row only). */
  onStop?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const first = prompt.parts[0];
  const toggle = () => setExpanded((v) => !v);
  return (
    <div className="message-bubble-user-wrap">
      <div className="message-bubble-user-row">
        <div
          className={`message-bubble-user-content message-bubble-automated is-${first?.kind || "message"}${expanded ? " is-expanded" : ""}`}
          role="button"
          tabIndex={0}
          aria-expanded={expanded}
          aria-label={`${first?.tag || "Message"} from ${first?.from || "another agent"}, not from you`}
          title={expanded ? "Collapse" : "Expand"}
          onClick={(event) => {
            if ((event.target as HTMLElement).closest?.(".chat-ref-chip, .message-bubble-automated-from")) return;
            toggle();
          }}
          onKeyDown={(event) => {
            if (event.target !== event.currentTarget) return;
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              toggle();
            }
          }}
        >
          {onStop ? (
            <div className="message-bubble-user-actions">
              <InlineStopButton onClick={onStop} />
            </div>
          ) : null}
          {prompt.heading ? <div className="message-bubble-automated-heading">{prompt.heading}</div> : null}
          {prompt.parts.map((part, index) => (
            <AutomatedPartView key={`${index}-${part.fromConvId || part.from}`} part={part} expanded={expanded} />
          ))}
        </div>
      </div>
    </div>
  );
});
