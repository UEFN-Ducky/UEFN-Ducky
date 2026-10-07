import { memo, useMemo } from "react";
import { Icons } from "../icons/Icons";
import {
  chatCollapseKey,
  useChatCollapseScope,
  useChatCollapseState,
} from "../hooks/useChatCollapseState";
import { ToolExecutionCard } from "./ToolExecutionCard";
import type { ActivityItem } from "../utils/chatMessageGroups";
import type { ChatTab, LinkedAgent, MessageAuthorDto } from "../types/panel";
import { replayShowMe, showMeLabel, showMeRequestFromTool } from "./tool-cards/bodies/ShowMeBody";
import { unwrapCodingAgentTool } from "../utils/unwrapCodingAgentTool";
import type { ShowMeRequest } from "../showme/ShowMeService";

interface AgentActivityGroupProps {
  items: ActivityItem[];
  author?: MessageAuthorDto;
  convId?: string;
  captureAskKeys?: boolean;
  onOpenChat?: (chat: ChatTab) => void;
  onStopLinked?: (childConvId: string) => void;
  onOpenFile?: (path: string, name: string, options?: { line?: number }) => void;
  allChats?: ChatTab[];
  liveLinkedAgents?: LinkedAgent[];
  externalAgent?: boolean;
}

function toolRunning(item: Extract<ActivityItem, { kind: "tool" }>): boolean {
  const status = item.result?.tool?.status ?? item.intent.tool?.status;
  if (status) return status === "pending" || status === "running";
  return !item.result;
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

/** Show me calls in the group that finished: each gets a button that stays visible folded. */
function showMeButtons(items: ActivityItem[]): Array<{ id: string; request: ShowMeRequest }> {
  const out: Array<{ id: string; request: ShowMeRequest }> = [];
  for (const item of items) {
    if (item.kind !== "tool" || toolRunning(item)) continue;
    const tool = item.result?.tool ?? item.intent.tool;
    const rawArgs = item.result?.tool?.arguments ?? item.intent.tool?.arguments ?? {};
    const call = unwrapCodingAgentTool(tool?.name ?? "", rawArgs && typeof rawArgs === "object" && !Array.isArray(rawArgs) ? rawArgs as Record<string, unknown> : {});
    const request = showMeRequestFromTool(call.name, call.arguments);
    if (request) out.push({ id: item.id, request });
  }
  return out;
}

/** Nested hubs as a breadcrumb, leaf name last. Strips a "Group — " prefix already baked into name. */
function speakerParts(author?: MessageAuthorDto): { path: string[]; name: string } {
  if (!author) return { path: [], name: "" };
  const path = (author.group_path ?? []).map((s) => s.trim()).filter(Boolean);
  let name = (author.name ?? "").trim();
  for (const group of path) {
    const prefix = `${group} — `;
    if (name.startsWith(prefix)) name = name.slice(prefix.length);
  }
  return { path, name };
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
  author,
  convId = "",
  captureAskKeys = false,
  onOpenChat,
  onStopLinked,
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
  const speaker = speakerParts(author);
  const shows = useMemo(() => showMeButtons(items), [items]);

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
          {speaker.name ? (
            <span
              className="agent-activity-group-speaker"
              style={{ ["--member-color" as string]: author?.color || "var(--accent)" }}
            >
              {speaker.path.map((crumb, i) => (
                <span key={`${i}-${crumb}`} className="agent-activity-group-crumb">
                  {crumb}
                  <span className="agent-activity-group-crumb-sep" aria-hidden="true">
                    ›
                  </span>
                </span>
              ))}
              {speaker.name}
            </span>
          ) : null}
          <span className="agent-activity-group-label">{label}</span>
          {!open && toolCount > 0 ? (
            <span className="agent-activity-group-meta">{toolCount}</span>
          ) : null}
          {live ? <span className="agent-activity-group-pulse" aria-hidden="true" /> : null}
        </button>
      </div>
      {shows.length ? (
        <div className="agent-activity-group-showme" role="group" aria-label="Show me">
          {shows.map(({ id, request }) => (
            <button key={id} type="button" className="tool-card-showme-button" title="Take me there and highlight it" onClick={() => void replayShowMe(request)}>
              <Icons.Sparkles />
              <span>Show me: {showMeLabel(request)}</span>
            </button>
          ))}
        </div>
      ) : null}
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
