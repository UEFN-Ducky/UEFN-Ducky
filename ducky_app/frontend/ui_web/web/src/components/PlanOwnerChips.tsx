import { useEffect, useState, useRef } from "react";
import type { AgentEvent, GroupMemberDto } from "../types/panel";
import { getApi } from "../hooks/usePanelApi";
import { subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { useRunningAgents } from "../hooks/useRunningAgents";
import { getChatTurnTimer } from "../hooks/chatTurnTimer";
import { getCachedChatMessages } from "../hooks/chatMessagesCache";
import { buildActivityLines, activityPanelTitle, splitTurnMessages } from "../utils/agentActivity";
import { requestOpenChatTab } from "../navigation/openChatReference";
import { requestLatestChatActivity } from "../navigation/latestChatActivity";
import { DuckyAvatar } from "./ducky/DuckyAvatars";
import "./PlanOwnerChips.css";

type Owner = { id: string; name: string; style?: string; members?: GroupMemberDto[] };
let cachedApi: ReturnType<typeof getApi>;
let directory: Promise<Owner[]> | undefined;
const groups = new Map<string, Promise<GroupMemberDto[]>>();
function owners(): Promise<Owner[]> {
  const api = getApi();
  if (cachedApi !== api) { cachedApi = api; directory = undefined; groups.clear(); }
  return directory ??= (api?.list_all_conversations?.(true) ?? Promise.resolve([])).then(rows => rows.map(row => ({
    id: row.id, name: row.title || row.ducky_name || row.id, style: row.ducky_style,
    members: row.is_group ? row.group_members || [] : undefined,
  }))).catch(() => { directory = undefined; return []; });
}
function members(id: string): Promise<GroupMemberDto[]> {
  if (!groups.has(id)) groups.set(id, (getApi()?.group_members?.(id) ?? Promise.resolve({ members: [] })).then(r => r.members || []).catch(() => { groups.delete(id); return []; }));
  return groups.get(id)!;
}

export function ownerActivity(event: AgentEvent): string | undefined {
  if (event.type === "agent_stopped" || event.type === "error") return "Idle";
  if (event.type === "status") return event.text || "Working";
  if (event.type === "tool" && event.tool) return buildActivityLines([{ id: 0, role: "tool", text: "", tool: { ...event.tool, status: "pending" } }], "", "")[0]?.text || "Working";
  if (event.type === "thinking") return "Thinking";
  if (event.type === "text_delta") return "Writing response";
  if (event.type === "tool_done") return "Working";
  return undefined;
}

export function PlanOwnerChips({ ownerId, activeOnly = false, nodeId }: { ownerId: string; activeOnly?: boolean; nodeId?: string }) {
  const [owner, setOwner] = useState<Owner>({ id: ownerId, name: ownerId });
  const tracked = useRef(new Set([ownerId]));
  const waiting = useRef(new Map<string, Set<string>>());
  const [activity, setActivity] = useState<Record<string, string>>({});
  const [now, setNow] = useState(Date.now());
  const running = useRunningAgents();
  useEffect(() => {
    let alive = true;
    async function refresh() {
      const rows = await owners();
      const found = rows.find(row => row.id === ownerId) || { id: ownerId, name: ownerId };
      const roster = found.members !== undefined ? await members(ownerId) : undefined;
      if (alive) {
        const resolved = { ...found, members: roster?.length ? roster : found.members };
        tracked.current = new Set([ownerId, ...(resolved.members || []).map(m => m.member_conv_id)]);
        setOwner(resolved);
      }
    }
    void refresh();
    const unsubscribe = subscribeAgentEvents(event => {
      if (event.type === "chats_changed" || event.type === "plan_assignment_changed") {
        directory = undefined; groups.clear(); void refresh();
      }
      const eventChat = event.type === "linked_agent" ? event.parent_conv_id || event.conv_id : event.conv_id;
      if (!eventChat || !tracked.current.has(eventChat)) return;
      if (event.type === "linked_agent" && event.child_conv_id) {
        const linked = waiting.current.get(eventChat) || new Set<string>();
        if (event.status === "running") linked.add(event.child_conv_id);
        else linked.delete(event.child_conv_id);
        waiting.current.set(eventChat, linked);
        setNow(Date.now());
      }
      if (event.type === "agent_stopped" || event.type === "error") waiting.current.delete(eventChat);
      const label = ownerActivity(event);
      if (event.conv_id && label !== undefined) setActivity(old => ({ ...old, [event.conv_id!]: label }));
    });
    return () => { alive = false; unsubscribe(); };
  }, [ownerId]);
  const assigned = nodeId ? owner.members?.filter(member => member.observation?.assignment?.node_id === nodeId) : undefined;
  const roster = assigned?.length ? assigned : owner.members || [];
  const chips: Owner[] = [owner, ...roster.map(member => ({ id: member.member_conv_id, name: member.name || member.ducky_name || member.member_conv_id, style: member.ducky_style }))];
  // Only a running chip shows a clock. A plan renders one of these per assigned step, and
  // each used to re-render every second whether anyone was working or not.
  const ticking = chips.some(chip => running.has(chip.id) && getChatTurnTimer(chip.id)?.startedAt);
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [ticking]);
  return <div className="plan-owner-chips" aria-label="Plan owners">
    {chips.filter(chip => !activeOnly || running.has(chip.id)).map(chip => {
      const isRunning = running.has(chip.id);
      const cached = getCachedChatMessages(chip.id);
      const lines = cached ? buildActivityLines(splitTurnMessages(cached.messages, isRunning).turnMessages, cached.streamBuffer, cached.streamThinking) : [];
      const awaiting = waiting.current.get(chip.id)?.size || 0;
      const label = isRunning ? awaiting ? "Waiting for reply" : activity[chip.id] || activityPanelTitle(lines, false, 0, undefined, cached?.run?.statusText || "Working") : "Idle";
      const start = getChatTurnTimer(chip.id)?.startedAt;
      const seconds = start ? Math.max(0, Math.floor((now - start) / 1000)) : 0;
      const duration = isRunning && start ? ` ${Math.floor(seconds / 60)}m ${seconds % 60}s` : "";
      return <button key={chip.id} type="button" className="plan-owner-chip" data-running={isRunning} onClick={() => {
        requestLatestChatActivity(chip.id);
        requestOpenChatTab(chip.id, chip.name);
      }} title={`Open ${chip.name} at latest activity`}>
        <DuckyAvatar styleId={chip.style} size={20} />
        <span>{chip.name}</span><span className="plan-owner-activity">{label}{duration}</span>
      </button>;
    })}
  </div>;
}
