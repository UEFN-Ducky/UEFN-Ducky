import { useState, useSyncExternalStore } from "react";

import { Icons } from "../icons/Icons";
import {
  answerTerminalApproval,
  listTerminalApprovals,
  subscribeTerminalApprovals,
  terminalApprovalChoices,
  type TerminalApproval,
} from "./terminalApprovals";
import type { PendingTerminalCommand, TerminalApprovalChoice } from "./types";

function choiceText(choice: TerminalApprovalChoice, item: PendingTerminalCommand): { label: string; desc: string } {
  switch (choice) {
    case "once":
      return { label: "Allow once", desc: "Run it this time. Ask again next time." };
    case "always":
      return { label: `Always allow ${item.rule_label} in this chat`, desc: "Don't ask again for this command in this chat." };
    case "all":
      return {
        label: "Allow everything in this chat",
        desc: "Never ask again in this chat: pushes, deletes and publishes run too. Turn it off in the chat's context panel.",
      };
    default:
      return { label: "Deny", desc: "Don't run it. The agent is told you said no." };
  }
}

type Props = {
  item: PendingTerminalCommand;
  /** The header's activity list: tighter, no pause hint. */
  compact?: boolean;
};

/** One agent command waiting for Allow/Deny, in the same look as the question cards. */
export function TerminalApprovalCard({ item, compact = false }: Props) {
  const [busy, setBusy] = useState<TerminalApprovalChoice | null>(null);
  const [error, setError] = useState("");
  const where = [item.cwd, item.shell].filter(Boolean).join(" · ");

  const answer = async (choice: TerminalApprovalChoice) => {
    if (busy) return;
    setBusy(choice);
    setError("");
    const result = await answerTerminalApproval(item.request_id, choice);
    if (!result.ok) {
      setError(result.error);
      setBusy(null);
    }
  };

  return (
    <div
      className={`ask-user-body terminal-approval-card${compact ? " terminal-approval-card--compact" : ""}`}
      role="group"
      aria-label="Terminal command approval"
      data-approval-request={item.request_id}
    >
      <div className="ask-user-top">
        <span className="ask-user-badge terminal-approval-badge" aria-hidden="true">
          <Icons.Terminal />
        </span>
        {compact ? null : <span className="ask-user-pause-hint">Agent paused until you answer</span>}
      </div>
      <h2 className="ask-user-prompt">Allow this terminal command?</h2>
      {item.local_only ? (
        <p className="ask-user-warning" role="note">
          <Icons.AlertTriangle />
          <span>This is a local-only AI plugin. It must never be pushed or published.</span>
        </p>
      ) : null}
      <pre className="ask-user-detail">{item.command}</pre>
      {where ? <p className="terminal-approval-where">Runs in {where}</p> : null}
      <div className="ask-user-options" aria-label="Answer">
        {terminalApprovalChoices(item).map((choice) => {
          const text = choiceText(choice, item);
          return (
            <button
              key={choice}
              type="button"
              className={`ask-user-option terminal-approval-option${busy === choice ? " is-selected" : ""}`}
              disabled={busy !== null}
              onClick={() => void answer(choice)}
            >
              <div className="ask-user-option-main">
                <span className="ask-user-option-label">{text.label}</span>
                {compact ? null : <span className="ask-user-option-desc">{text.desc}</span>}
              </div>
            </button>
          );
        })}
      </div>
      {error ? (
        <p className="terminal-approval-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function useApprovalsForConv(convId: string): TerminalApproval[] {
  const all = useSyncExternalStore(subscribeTerminalApprovals, listTerminalApprovals, listTerminalApprovals);
  const cid = (convId || "").trim();
  return cid ? all.filter((item) => item.conv_id === cid) : [];
}

export function useHasTerminalApprovals(convId: string): boolean {
  const cid = (convId || "").trim();
  const read = () => Boolean(cid) && listTerminalApprovals().some((item) => item.conv_id === cid);
  return useSyncExternalStore(subscribeTerminalApprovals, read, read);
}

/** The chat's waiting command cards, at the end of its conversation. */
export function ChatTerminalApprovals({ convId }: { convId: string }) {
  const items = useApprovalsForConv(convId);
  if (!items.length) return null;
  return (
    <>
      {items.map((item) => (
        <div
          key={item.request_id}
          className="virtual-chat-message-list-row virtual-chat-ask-row"
          data-pending-card="command"
        >
          <div className="virtual-chat-ask-panel">
            <TerminalApprovalCard item={item} />
          </div>
        </div>
      ))}
    </>
  );
}
