import type {
  AgentProfileDto,
  ChatPlan,
  ChatTab,
  FolderItem,
  GroupMemberDto,
  GroupMemberObservation,
  PanelApi,
  PlanNode,
} from "../../types/panel";
import { modelFromFavorites } from "../ducky/duckyProfileForm";
import { resolveNestedGroupHoverRows, shortModelLabel } from "../groupMemberHover";
import { loadRoster } from "./rosterStore";

/** Names of the running agents a leader's team holds (its group and nested groups), or []
 * when `chatId` leads no group in the sidebar tree. */
export function runningTeamNames(chatId: string, folders: FolderItem[], running: ReadonlySet<string>): string[] {
  return runningTeamChats(chatId, folders, running).map((c) => c.name);
}

/** Same walk as `runningTeamNames`, keeping each agent's chat id. */
export function runningTeamChats(
  chatId: string,
  folders: FolderItem[],
  running: ReadonlySet<string>,
): { convId: string; name: string }[] {
  const chats = (f: FolderItem): { convId: string; name: string }[] => [
    ...f.chats.filter((c) => c.id !== chatId && running.has(c.id)).map((c) => ({ convId: c.id, name: c.name })),
    ...f.children.flatMap(chats),
  ];
  for (const f of folders) {
    if (f.groupHubId && f.chats.some((c) => c.id === chatId && c.isLeader)) return chats(f);
    const nested = runningTeamChats(chatId, f.children, running);
    if (nested.length) return nested;
  }
  return [];
}

/** One row of a group chat's team: an agent, or a nested group with its own rows. */
export type RosterNode = {
  /** Unique in the tree: the group it sits in + its chat id. */
  key: string;
  /** The group this row belongs to; model, lane and remove go to that group. */
  hubId: string;
  /** Null for a read-only row (a nested group's agents read from the sidebar tree). */
  member: GroupMemberDto | null;
  id: string;
  name: string;
  /** Hover for the name: the library profile and the model ("Verse Coder · gpt-5"). */
  hover?: string;
  styleId?: string;
  model: string;
  codingAgent: string;
  /** API provider of an embedded (ducky) member: decides how its turns are billed. */
  provider?: string;
  contextTokens: number;
  isGroup: boolean;
  isLeader: boolean;
  /** Collapsed to one progress row; a muted group hides its rows. */
  muted: boolean;
  color?: string;
  /** Short "when to use" from the ducky's profile. */
  blurb: string;
  depth: number;
  children: RosterNode[];
};

export type RosterInput = {
  groupId: string;
  members: GroupMemberDto[];
  leaderConvId: string;
  /** Nested group id → its members, as the bridge returned them. */
  rosters: Record<string, GroupMemberDto[]>;
  folders: FolderItem[];
  allChats: ChatTab[];
  profileById: Map<string, AgentProfileDto>;
  labelFor: (styleId?: string | null) => string;
};

/** The seat name (Search lead), then the duck name, then the library profile title; the avatar style last. */
export function memberDisplayName(
  m: GroupMemberDto,
  profile: AgentProfileDto | undefined,
  labelFor: (styleId?: string | null) => string,
): string {
  if (m.is_group) return (m.name || m.ducky_name || "Group").trim() || "Group";
  return (
    (m.name || "").trim() ||
    (m.ducky_name || "").trim() ||
    (profile?.name || "").trim() ||
    labelFor(m.ducky_style || profile?.ducky_style)
  );
}

/** The name's hover: which library profile fills the seat, and its model. */
export function memberHover(profile: AgentProfileDto | undefined, model: string): string {
  return [(profile?.name || "").trim(), shortModelLabel(model)].filter(Boolean).join(" · ");
}

const MAX_DEPTH = 4;

/** The whole team as a tree: direct members first, nested groups with their own agents. */
export function buildRoster(input: RosterInput): RosterNode[] {
  const chatById = new Map(input.allChats.map((c) => [c.id, c]));
  const seen = new Set<string>([input.groupId]);

  const nodeFor = (m: GroupMemberDto, hubId: string, leaderId: string, depth: number): RosterNode => {
    const chat = chatById.get(m.member_conv_id);
    const profile = input.profileById.get(m.profile_id);
    const isGroup = Boolean(m.is_group);
    const model = (m.model || "").trim() || (chat?.model || "").trim() || modelFromFavorites(profile?.favorite_models);
    const node: RosterNode = {
      key: `${hubId}/${m.member_conv_id}`,
      hubId,
      member: m,
      id: m.member_conv_id,
      name: memberDisplayName(m, profile, input.labelFor),
      ...(isGroup ? {} : { hover: memberHover(profile, model) }),
      styleId: m.ducky_style || profile?.ducky_style || chat?.duckyStyle,
      model,
      codingAgent: (m.coding_agent || "").trim() || (chat?.codingAgent || "").trim() || "ducky",
      provider: (chat?.provider || "").trim(),
      contextTokens: Math.max(0, Number(chat?.contextTokens) || 0),
      isGroup,
      isLeader: !isGroup && Boolean(leaderId) && m.member_conv_id === leaderId,
      muted: Boolean(chat?.muted),
      color: m.color,
      blurb: isGroup ? "" : (profile?.when_to_use || profile?.ducky_personality || chat?.duckyPersonality || "").trim(),
      depth,
      children: [],
    };
    if (isGroup && depth < MAX_DEPTH && !seen.has(node.id)) {
      seen.add(node.id);
      node.children = childrenOf(node.id, depth + 1);
    }
    return node;
  };

  const childrenOf = (hubId: string, depth: number): RosterNode[] => {
    const fromBridge = input.rosters[hubId];
    if (fromBridge && fromBridge.length > 0) {
      const leader = (chatById.get(hubId)?.leaderConvId || "").trim();
      return fromBridge.map((m) => nodeFor(m, hubId, leader, depth));
    }
    // No roster from the bridge yet: show what the sidebar tree knows, read-only.
    return resolveNestedGroupHoverRows(hubId, input.folders, input.allChats, [], input.profileById, input.labelFor).map(
      (row) => ({
        key: `${hubId}/${row.id}`,
        hubId,
        member: null,
        id: row.id,
        name: row.name,
        styleId: row.duckyStyle,
        model: row.model,
        codingAgent: row.codingAgent,
        contextTokens: row.contextTokens,
        isGroup: row.isGroup,
        isLeader: false,
        muted: Boolean(chatById.get(row.id)?.muted),
        blurb: "",
        depth,
        children: [],
      }),
    );
  };

  return input.members.map((m) => nodeFor(m, input.groupId, input.leaderConvId, 0));
}

/** Every agent (not group) in the tree, in display order. */
export function rosterAgents(nodes: RosterNode[]): RosterNode[] {
  const out: RosterNode[] = [];
  const walk = (list: RosterNode[]) => {
    for (const n of list) {
      if (n.isGroup) walk(n.children);
      else out.push(n);
    }
  };
  walk(nodes);
  return out;
}

/** Context of a row: its own, or the sum of every agent inside a group. */
export function rosterTokens(node: RosterNode): number {
  if (!node.isGroup) return node.contextTokens;
  return rosterAgents(node.children).reduce((sum, a) => sum + a.contextTokens, 0);
}

/** Rows matching the filter (name, model or coding agent); a group stays when any row inside matches. */
export function filterRoster(nodes: RosterNode[], query: string): RosterNode[] {
  const q = query.trim().toLowerCase();
  if (!q) return nodes;
  const hit = (n: RosterNode) =>
    n.name.toLowerCase().includes(q) ||
    shortModelLabel(n.model).toLowerCase().includes(q) ||
    n.codingAgent.toLowerCase().replace(/_/g, " ").includes(q);
  const out: RosterNode[] = [];
  for (const n of nodes) {
    if (n.isGroup) {
      const children = hit(n) ? n.children : filterRoster(n.children, q);
      if (hit(n) || children.length > 0) out.push({ ...n, children });
    } else if (hit(n)) {
      out.push(n);
    }
  }
  return out;
}

export type SubPlanProgress = {
  /** The sub plan (or step) the agent is assigned. */
  title: string;
  done: number;
  /** Steps that count: cancelled ones are left out. */
  total: number;
  pct: number;
  /** The step in progress, else the next one waiting; empty when all are done. */
  current: string;
  /** The unfinished step the next step waits for (its ``after``), when nothing is in progress. */
  waitingOn?: string;
};

type Assignment = NonNullable<GroupMemberObservation["assignment"]>;

/** Plan rows carry ``after`` (ids of steps that must finish first). */
type PlanNodeWithAfter = PlanNode & { after?: string[] };

function findPlanNode(nodes: PlanNode[] | undefined, id: string): PlanNode | undefined {
  for (const node of nodes || []) {
    if (node.id === id) return node;
    const inside = findPlanNode(node.children, id);
    if (inside) return inside;
  }
  return undefined;
}

function planLeaves(node: PlanNode): PlanNode[] {
  const kids = node.children || [];
  return kids.length ? kids.flatMap(planLeaves) : [node];
}

/** The leaves an assignment covers (cancelled left out); null when its plan or node is gone. */
function assignedLeaves(plan: ChatPlan | null | undefined, assignment: Assignment | null | undefined) {
  if (!plan || !assignment || plan.id !== assignment.plan_id) return null;
  const root = assignment.node_id ? findPlanNode(plan.nodes, assignment.node_id) : undefined;
  if (assignment.node_id && !root) return null;
  const leaves = root ? planLeaves(root) : (plan.nodes || []).flatMap(planLeaves);
  return { root, leaves: leaves.filter((n) => n.status !== "cancelled") };
}

/**
 * An agent's progress through its assigned part of the team plan: steps done of total, %, and the
 * current step. Null when the plan is not the one the assignment names (an old or moved plan).
 */
export function subPlanProgress(
  plan: ChatPlan | null | undefined,
  assignment: GroupMemberObservation["assignment"] | undefined,
): SubPlanProgress | null {
  const found = assignedLeaves(plan, assignment);
  if (!plan || !assignment || !found || found.leaves.length === 0) return null;
  const { root, leaves: counted } = found;
  const done = counted.filter((n) => n.status === "completed").length;
  const running = counted.find((n) => n.status === "in_progress");
  const current = running ?? counted.find((n) => n.status === "pending");
  let waitingOn: string | undefined;
  if (!running && current) {
    const blocker = ((current as PlanNodeWithAfter).after || [])
      .map((id) => findPlanNode(plan.nodes, id))
      .find((n) => n && n.status !== "completed" && n.status !== "cancelled");
    if (blocker) waitingOn = blocker.content;
  }
  return {
    title: root?.content || plan.title || assignment.title,
    done,
    total: counted.length,
    pct: Math.round((done / counted.length) * 100),
    current: current?.content || "",
    ...(waitingOn ? { waitingOn } : {}),
  };
}

/** Several agents' parts added up; a part shared by two agents counts once. */
export function sumProgress(parts: SubPlanProgress[], title: string): SubPlanProgress | null {
  const done = parts.reduce((n, p) => n + p.done, 0);
  const total = parts.reduce((n, p) => n + p.total, 0);
  if (!total) return null;
  const open = parts.filter((p) => p.current);
  return {
    title,
    done,
    total,
    pct: Math.round((done / total) * 100),
    current: open.length === 1 ? open[0].current : open.length ? `${open.length} parts open` : "",
  };
}

/** Where a row's assignment comes from: by default the observation on its own member row. */
export type ObservationOf = (node: RosterNode) => GroupMemberObservation | undefined;

const ownObservation: ObservationOf = (node) => node.member?.observation;

/** The row's own assignment; a lead with none (the plan's owner) covers its whole plan. */
function rowAssignment(
  node: RosterNode,
  plans: Record<string, ChatPlan | null | undefined>,
  observationOf: ObservationOf,
): Assignment | null {
  const observation = observationOf(node);
  const assignment = observation && observation.group_id === node.hubId ? observation.assignment : null;
  if (assignment || !node.isLeader) return assignment;
  const plan = plans[node.id];
  return plan ? { plan_id: plan.id, chat_id: node.id, node_id: null, title: plan.title, status: "" } : null;
}

/**
 * Rolled-up progress of a row: an agent's own sub plan; a group's own sub plans plus every row
 * inside it, each plan step counted once even when two rows share (or nest) a sub plan; the lead
 * row covers the whole plan. Null when nothing in it is assigned.
 */
export function rollupProgress(
  node: RosterNode,
  plans: Record<string, ChatPlan | null | undefined>,
  observationOf: ObservationOf = ownObservation,
): SubPlanProgress | null {
  if (!node.isGroup) {
    const assignment = rowAssignment(node, plans, observationOf);
    return assignment ? subPlanProgress(plans[assignment.chat_id], assignment) : null;
  }
  const leaves = new Map<string, PlanNode>();
  const parts = new Map<string, SubPlanProgress>();
  const walk = (row: RosterNode) => {
    const assignment = rowAssignment(row, plans, observationOf);
    const plan = assignment ? plans[assignment.chat_id] : undefined;
    const found = assignedLeaves(plan, assignment);
    if (assignment && found) {
      for (const leaf of found.leaves) leaves.set(`${assignment.plan_id}:${leaf.id}`, leaf);
      const p = subPlanProgress(plan, assignment);
      if (p) parts.set(`${assignment.plan_id}:${assignment.node_id ?? ""}`, p);
    }
    row.children.forEach(walk);
  };
  walk(node);
  if (leaves.size === 0) return null;
  const done = [...leaves.values()].filter((n) => n.status === "completed").length;
  const open = [...parts.values()].filter((p) => p.current);
  return {
    title: node.name,
    done,
    total: leaves.size,
    pct: Math.round((done / leaves.size) * 100),
    current: open.length === 1 ? open[0].current : open.length ? `${open.length} parts open` : "",
  };
}

type ProgressApi = Partial<Pick<PanelApi, "group_members" | "get_plan">>;

/**
 * A sidebar row's progress read from the bridge: a chat's own sub plan (from its group's roster),
 * or a group's rollup over every agent in it and its nested groups.
 */
export async function loadRowProgress(
  api: ProgressApi | null | undefined,
  row: { id: string; name: string; isGroup: boolean; parentHubId?: string },
): Promise<SubPlanProgress | null> {
  if (!api?.group_members || !api.get_plan) return null;
  // The shared roster store: a sidebar row, the team bar and the chat read one cached copy.
  const membersOf = async (id: string) => (await loadRoster(id, api)).members;
  const rosters: Record<string, GroupMemberDto[]> = {};
  let members: GroupMemberDto[] = [];
  if (row.isGroup) {
    members = await membersOf(row.id);
    const queue = members.filter((m) => m.is_group).map((m) => m.member_conv_id);
    for (let i = 0; i < queue.length && i < 50; i++) {
      const id = queue[i];
      if (rosters[id]) continue;
      rosters[id] = await membersOf(id);
      queue.push(...rosters[id].filter((m) => m.is_group).map((m) => m.member_conv_id));
    }
  } else if (row.parentHubId) {
    members = (await membersOf(row.parentHubId)).filter((m) => m.member_conv_id === row.id);
  }
  const owners = new Set(
    [members, ...Object.values(rosters)].flat().map((m) => m.observation?.assignment?.chat_id || "").filter(Boolean),
  );
  const plans: Record<string, ChatPlan | null> = {};
  for (const id of owners) {
    const res = await api.get_plan(id).catch(() => null);
    plans[id] = res?.ok ? (res as { plan?: ChatPlan | null }).plan ?? null : null;
  }
  const nodes = buildRoster({
    groupId: row.isGroup ? row.id : row.parentHubId || "",
    members,
    leaderConvId: "",
    rosters,
    folders: [],
    allChats: [],
    profileById: new Map(),
    labelFor: () => "",
  });
  if (!row.isGroup) return nodes[0] ? rollupProgress(nodes[0], plans) : null;
  // ponytail: the group's own sub plan lives in its parent's roster; the team bar counts it, the sidebar row doesn't.
  return rollupProgress(
    { key: row.id, hubId: "", member: null, id: row.id, name: row.name, model: "", codingAgent: "", contextTokens: 0,
      isGroup: true, isLeader: false, muted: true, blurb: "", depth: 0, children: nodes },
    plans,
  );
}

/** Nested group ids anywhere in the tree (to fetch their rosters). */
export function rosterGroupIds(nodes: RosterNode[]): string[] {
  const out: string[] = [];
  const walk = (list: RosterNode[]) => {
    for (const n of list) {
      if (!n.isGroup) continue;
      out.push(n.id);
      walk(n.children);
    }
  };
  walk(nodes);
  return out;
}
