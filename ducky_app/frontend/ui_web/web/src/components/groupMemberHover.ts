import { parseFavoriteSelection } from "../hooks/favoriteModelsCatalog";
import type { AgentProfileDto, ChatTab, ChangesetRunDto, FolderItem, GroupMemberDto } from "../types/panel";
import {
  findFolderByHubId,
  summarizeFolderContext,
  type FolderContextAgent,
} from "../utils/folderContextSummary";
import { modelFromFavorites } from "./ducky/duckyProfileForm";

/** A roster read is a snapshot, not a heartbeat or proof of task completion. */
export function memberObservationLines(member: GroupMemberDto, groupId: string, now: number): string[] {
  const o = member.observation;
  if (!o || o.group_id !== groupId) return ["Runtime: unknown", "Assignment: unknown"];
  const age = now / 1000 - o.observed_at;
  const stale = !Number.isFinite(age) || age < 0 || age > 60;
  const task = o.assignment;
  return [
    `Group: ${o.group_id} · Role: ${o.role}`,
    `Project: ${o.project_slug || "unknown"}`,
    stale ? "Runtime: unknown (stale observation)" : `Runtime: ${o.runtime} (snapshot; not task completion)`,
    `Observed: ${Number.isFinite(o.observed_at) ? new Date(o.observed_at * 1000).toLocaleTimeString() : "unknown"}`,
    task ? `${stale ? "Last observed assignment" : "Assignment"}: ${task.title} [${task.status}] · plan ${task.plan_id}${task.node_id ? ` · node ${task.node_id}` : ""}` : "Assignment: unknown",
    "Heartbeat, blocker and review verdict: unknown",
  ];
}

/** Only recorded journal facts; no inferred task binding or present authorship. */
export function memberFileLines(runs: ChangesetRunDto[] | null, convId: string, readAt: number, now: number): string[] {
  const limits = "Current file state, external activity and enforced locks: unknown";
  if (runs === null) return ["File history: unavailable", limits];
  const records = runs.filter(run => run.conv_id === convId)
    .flatMap(run => (run.entries || []).map(entry => ({ run, entry })))
    .sort((a, b) => b.entry.ts - a.entry.ts);
  const latest = records[0];
  if (!latest) return ["File history: no records returned", limits];
  const { run, entry } = latest;
  const stale = !Number.isFinite(readAt) || now < readAt || now - readAt > 60000;
  return [
    `File history: latest returned entry${stale ? " (stale snapshot)" : ""}`,
    `Recorded owner: ${run.conv_id} · run ${run.run_id}`,
    `Recorded path: ${entry.path} · ${entry.op} · outcome ${entry.outcome || "unknown"}`,
    `Recorded time: ${Number.isFinite(entry.ts) ? new Date(entry.ts * 1000).toLocaleString() : "unknown"}`,
    "Recorded task binding and review verdict: unknown",
    limits,
  ];
}

export type GroupHoverRow = {
  id: string;
  name: string;
  duckyStyle?: string;
  model: string;
  codingAgent: string;
  contextTokens: number;
  isGroup: boolean;
};

export function shortModelLabel(raw: string): string {
  const text = (raw || "").trim();
  if (!text) return "Default model";
  const parsed = parseFavoriteSelection(text);
  return parsed?.modelId || text;
}

/** "claude-haiku" or "cursor · claude-haiku" when a coding agent is set. */
export function aiTypeLabel(model?: string, codingAgent?: string): string {
  const modelLabel = shortModelLabel(model || "");
  const agent = (codingAgent || "").trim().toLowerCase().replace(/-/g, "_");
  if (!agent || agent === "ducky") return modelLabel;
  const agentLabel = agent.replace(/_/g, " ");
  return modelLabel !== "Default model" ? `${agentLabel} · ${modelLabel}` : agentLabel;
}

function memberName(
  m: GroupMemberDto,
  chat: ChatTab | undefined,
  profile: AgentProfileDto | undefined,
  labelFor: (styleId?: string | null) => string,
): string {
  if (m.is_group) return (m.name || m.ducky_name || "Group").trim() || "Group";
  const fromProfile = (profile?.name || "").trim();
  if (fromProfile) return fromProfile;
  const fromRole = (m.name || "").trim();
  if (fromRole) return fromRole;
  const fromMember = (m.ducky_name || "").trim();
  if (fromMember) return fromMember;
  const fromChat = (chat?.name || chat?.duckyName || "").trim();
  if (fromChat) return fromChat;
  return labelFor(m.ducky_style || chat?.duckyStyle || profile?.ducky_style);
}

function agentsToRows(agents: FolderContextAgent[], allChats: ChatTab[]): GroupHoverRow[] {
  const chatById = new Map(allChats.map((c) => [c.id, c]));
  return agents.map((a) => {
    const chat = chatById.get(a.id);
    return {
      id: a.id,
      name: a.name,
      duckyStyle: a.duckyStyle,
      model: a.model || (chat?.model || "").trim(),
      codingAgent:
        (a.codingAgent || "").trim() || (chat?.codingAgent || "").trim() || "ducky",
      contextTokens: a.contextTokens,
      isGroup: false,
    };
  });
}

/** Resolve nested-group roster for In-this-chat hover (folder tree first). */
export function resolveNestedGroupHoverRows(
  hubId: string,
  folders: FolderItem[],
  allChats: ChatTab[],
  apiMembers: GroupMemberDto[],
  profileById: Map<string, AgentProfileDto>,
  labelFor: (styleId?: string | null) => string,
): GroupHoverRow[] {
  const id = (hubId || "").trim();
  if (!id) return [];

  const folder = findFolderByHubId(folders, id);
  if (folder) {
    const summary = summarizeFolderContext(folder);
    const fromFolder = agentsToRows(summary.agents, allChats);
    // Nested subgroups as rows (name + rolled tokens); expand their leaf agents too.
    const fromSubs = summary.subgroups.flatMap((g) => agentsToRows(g.agents, allChats));
    if (fromFolder.length + fromSubs.length > 0) return [...fromFolder, ...fromSubs];
  }

  const chatById = new Map(allChats.map((c) => [c.id, c]));
  const hub = chatById.get(id);
  const raw: GroupMemberDto[] =
    hub?.groupMembers && hub.groupMembers.length > 0
      ? hub.groupMembers
      : apiMembers.length > 0
        ? apiMembers
        : [];

  if (raw.length > 0) {
    return raw.map((m) => {
      const chat = chatById.get(m.member_conv_id);
      const profile = profileById.get(m.profile_id);
      const model =
        (m.model || "").trim() ||
        (chat?.model || "").trim() ||
        modelFromFavorites(profile?.favorite_models);
      const codingAgent =
        (m.coding_agent || "").trim() || (chat?.codingAgent || "").trim() || "ducky";
      return {
        id: m.member_conv_id,
        name: memberName(m, chat, profile, labelFor),
        duckyStyle: m.ducky_style || chat?.duckyStyle || profile?.ducky_style,
        model,
        codingAgent,
        contextTokens: Math.max(0, Number(chat?.contextTokens) || 0),
        isGroup: Boolean(m.is_group || chat?.isGroup),
      };
    });
  }

  return allChats
    .filter((c) => (c.parentConvId || "").trim() === id)
    .map((c) => ({
      id: c.id,
      name: (c.name || c.duckyName || "Ducky").trim() || "Ducky",
      duckyStyle: c.duckyStyle,
      model: (c.model || "").trim(),
      codingAgent: (c.codingAgent || "").trim() || "ducky",
      contextTokens: Math.max(0, Number(c.contextTokens) || 0),
      isGroup: Boolean(c.isGroup),
    }));
}
