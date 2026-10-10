import { useEffect, useState, useSyncExternalStore } from "react";

import { AskUserForm, getAskUserSession, settleAskUser, subscribeAskUser, type AskUserSession } from "../ask-user";
import { chatTitleFor } from "../hooks/agentBackgroundActivity";
import { openWaitingChat, useWaitingItems, type WaitingItem } from "../hooks/waitingChats";
import { TerminalApprovalCard } from "../terminal/TerminalApprovalCard";
import { listTerminalApprovals, subscribeTerminalApprovals } from "../terminal/terminalApprovals";

function detailFor(item: WaitingItem): string {
  const text = item.text.replace(/\s+/g, " ").trim();
  const short = text.length > 90 ? `${text.slice(0, 88)}…` : text;
  return item.kind === "command" ? `Command: ${short}` : `Question: ${short}`;
}

/**
 * "Waiting for you" at the top of the header's background activity list: every chat blocked
 * on a question or command card (click opens it at the card), and the cards no chat owns
 * (a workflow's command, a question asked with no chat open), answered right here.
 */
export function WaitingForYouSection({ onOpenChat }: { onOpenChat: () => void }) {
  const items = useWaitingItems();
  const approvals = useSyncExternalStore(subscribeTerminalApprovals, listTerminalApprovals, listTerminalApprovals);
  const [orphan, setOrphan] = useState<AskUserSession | null>(() => getAskUserSession());
  useEffect(() => {
    setOrphan(getAskUserSession());
    return subscribeAskUser(() => setOrphan(getAskUserSession()));
  }, []);
  if (!items.length) return null;

  return (
    <section className="bg-activity-waiting" aria-label="Waiting for you">
      <div className="connection-status-menu-head">Waiting for you</div>
      {items.map((item) => {
        if (item.convId) {
          const title = chatTitleFor(item.convId) || "A chat";
          return (
            <div
              key={item.key}
              className="connection-status-menu-row bg-activity-waiting-row"
              role="button"
              tabIndex={0}
              data-waiting-item={item.key}
              title="Open the chat at its card"
              onClick={() => {
                onOpenChat();
                openWaitingChat(item.convId, title);
              }}
              onKeyDown={(event) => {
                if (event.target !== event.currentTarget || (event.key !== "Enter" && event.key !== " ")) return;
                event.preventDefault();
                onOpenChat();
                openWaitingChat(item.convId, title);
              }}
            >
              <span className="chat-waiting-marker chat-waiting-marker--chip" aria-hidden="true">?</span>
              <div className="connection-status-menu-text">
                <span className="connection-status-menu-label">{title}</span>
                <span className="connection-status-menu-detail">{detailFor(item)}</span>
              </div>
            </div>
          );
        }
        if (item.kind === "command") {
          const approval = approvals.find((row) => row.request_id === item.id);
          if (!approval) return null;
          return (
            <div key={item.key} className="bg-activity-waiting-card" data-waiting-item={item.key}>
              <span className="connection-status-menu-detail">Asked outside a chat</span>
              <TerminalApprovalCard item={approval} compact />
            </div>
          );
        }
        if (!orphan || orphan.id !== item.id) return null;
        return (
          <div key={item.key} className="bg-activity-waiting-card" data-waiting-item={item.key}>
            <AskUserForm
              questions={orphan.questions}
              title={orphan.title}
              queueAhead={orphan.queueAhead}
              author={orphan.author}
              captureKeys={false}
              showDismiss
              onComplete={(result) => settleAskUser(result, orphan.id)}
            />
          </div>
        );
      })}
    </section>
  );
}
