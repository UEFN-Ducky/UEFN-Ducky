import { memo, useMemo } from "react";
import { Icons } from "../icons/Icons";
import {
  chatCollapseKey,
  useChatCollapseScope,
  useChatCollapseState,
} from "../hooks/useChatCollapseState";
import { ToolExecutionCard } from "./ToolExecutionCard";
import type { ActivityItem } from "../utils/chatMessageGroups";
import type { ChatTab, LinkedAgent } from "../types/panel";
import { InlineStopButton } from "./inlineStopButton";

interface AgentActivityGroupProps {
  items: ActivityItem[];
  convId?: string;
  captureAskKeys?: boolean;
  onOpenChat?: (chat: ChatTab) => void;
  onStopLinked?: (childConvId: string) => void;
  onStop?: () => void;
  onOpenFile?: (path: string, name: string, options?: { line?: number }) => void;
  allChats?: ChatTab[];
  liveLinkedAgents?: LinkedAgent[];
  externalAgent?: boolean;
}

function toolRunning(item: Extract<ActivityItem, { kind: "tool" }>): boolean {
  const status = item.result?.tool?.status ?? item.intent.tool?.status;
  if (status === "cancelled") return false;
  if (!item.result) return true;
  return status === "pending";
}

function activityGroupLabel(items: ActivityItem[], live: boolean): string {
  const toolCount = items.reduce((n, i) => n + (i.kind === "tool" ? 1 : 0), 0);
  const thinkCount = items.length - toolCount;
  if (live && thinkCount > 0 && toolCount === 0) return "Thinking…";
  if (live && toolCount > 0) {
    const done = items.filter(
      (i) => i.kind === "tool" && !toolRunning(i),
    ).length;
    return done < toolCount
      ? `Running tools… ${done}/${toolCount}`
      : thinkCount > 0
        ? `Thought process · ${toolCount} tools`
        : `${toolCount} tools`;
  }
  const parts: string[] = [];
  if (thinkCount > 0) parts.push("Thought process");
  if (toolCount === 1) parts.push("1 tool");
  else if (toolCount > 1) parts.push(`${toolCount} tools`);
  return parts.join(" · ") || "Activity";
}

/**
 * Cursor-style accordion: consecutive thoughts + tools share one collapsed header
 * so a long agent ladder doesn't eat the whole viewport.
 *
 * Open state is sticky (user click only). New tools / live↔idle must not
 * auto-expand or auto-collapse — that caused constant open/close flicker.
 */
export const AgentActivityGroup = memo(function AgentActivityGroup({
  items,
  convId = "",
  captureAskKeys = false,
  onOpenChat,
  onStopLinked,
  onStop,
  onOpenFile,
  allChats = [],
  liveLinkedAgents = [],
  externalAgent = false,
}: AgentActivityGroupProps) {
  const collapseScope = useChatCollapseScope();
  const openKey = chatCollapseKey(collapseScope, "activity-group");

  const live = useMemo(
    () => items.some((i) => i.kind === "tool" && toolRunning(i)),
    [items],
  );

  const [open, setOpen] = useChatCollapseState(openKey, false);
  const toolCount = items.reduce((n, i) => n + (i.kind === "tool" ? 1 : 0), 0);
  const label = activityGroupLabel(items, live);

  return (
    <div className={`agent-activity-group${live ? " agent-activity-group--live" : ""}`}>
      <div className="agent-activity-group-header-row">
        <button
          type="button"
          className="agent-activity-group-header"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          <span className={`agent-activity-group-caret${open ? " is-open" : ""}`}>
            <Icons.ChevronDown />
          </span>
          <span className="agent-activity-group-label">{label}</span>
          {!open && toolCount > 0 ? (
            <span className="agent-activity-group-meta">{toolCount}</span>
          ) : null}
          {live ? <span className="agent-activity-group-pulse" aria-hidden="true" /> : null}
        </button>
        {live && onStop ? <InlineStopButton onClick={onStop} /> : null}
      </div>
      {open ? (
        <div className="agent-activity-group-body">
          {items.map((item) =>
            item.kind === "thinking" ? (
              <div
                key={item.id}
                className={`agent-activity-group-thought${item.isStreaming ? " agent-activity-group-thought--streaming" : ""}`}
              >
                {item.text.trim()}
              </div>
            ) : (
              <ToolExecutionCard
                key={item.id}
                intent={item.intent}
                result={item.result}
                convId={convId}
                captureKeys={captureAskKeys}
                embedded
                onOpenChat={onOpenChat}
                onStopLinked={onStopLinked}
                onOpenFile={onOpenFile}
                allChats={allChats}
                liveLinkedAgents={liveLinkedAgents}
                externalAgent={externalAgent}
              />
            ),
          )}
        </div>
      ) : null}
    </div>
  );
});
