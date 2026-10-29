import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from "react";
import { BillingBadge } from "./BillingBadge";
import { billingKind } from "../utils/billing";

import { subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { requestReloadDuckies } from "../hooks/useChatsChanged";
import { getApi } from "../hooks/usePanelApi";
import { useRunningAgents } from "../hooks/useRunningAgents";
import { useWaitingChatIds } from "../hooks/waitingChats";
import { WaitingMarker } from "./WaitingMarker";
import { requestOpenSettings } from "../navigation/openSettingsTab";
import type {
  AgentProfileDto,
  ChatPlan,
  ChatTab,
  ChangesetRunDto,
  FolderItem,
  GroupMemberDto,
  LaneCheckResult,
} from "../types/panel";
import { conflictCountsByConv } from "../utils/changesetGrouping";
import { fmtCompactTokens } from "../utils/contextFormat";
import { parseLaneText, shortLaneLabel, validateLaneGlobs } from "../utils/laneGlob";
import { findFolderByHubId } from "../utils/folderContextSummary";
import { numberedEntryName } from "../utils/numberedEntryName";
import { chatFolderSiblingNames } from "../utils/sidebarTree";
import { Icons } from "../icons/Icons";
import { DuckyAvatar, DUCKY_AVATAR_SIZES } from "./ducky/DuckyAvatars";
import { useDuckyCatalogOptional } from "./ducky/DuckyCatalogContext";
import { DuckyModelPicker } from "./ducky/DuckyModelPicker";
import { aiTypeLabel, memberFileLines, memberObservationLines } from "./groupMemberHover";
import {
  buildRoster,
  filterRoster,
  rollupProgress,
  rosterAgents,
  rosterGroupIds,
  rosterTokens,
  type RosterNode,
  type SubPlanProgress,
} from "./team/teamRoster";
import { refreshRoster, setRoster, useRoster, useRosters } from "./team/rosterStore";

export { shortModelLabel } from "./groupMemberHover";
export { subPlanProgress, sumProgress, type SubPlanProgress } from "./team/teamRoster";
export { parseLaneText, shortLaneLabel, validateLaneGlobs } from "../utils/laneGlob";

/** Settings → write lanes: enforce refuses out-of-lane writes, shadow only flags them. */
export type LaneMode = "enforce" | "shadow" | "off";

/** The app's lane mode from its settings; the backend default (enforce) when it is not reported. */
export function laneModeOf(settings: unknown): { mode: LaneMode; reported: boolean } {
  const raw = String((settings as { write_lanes_mode?: unknown } | null)?.write_lanes_mode ?? "").trim().toLowerCase();
  return raw === "enforce" || raw === "shadow" || raw === "off"
    ? { mode: raw, reported: true }
    : { mode: "enforce", reported: false };
}

/** The badge next to a lane: what happens to a write outside it. */
export function laneModeBadge(mode: LaneMode): string {
  return mode === "enforce" ? "Blocks" : mode === "shadow" ? "Warns only" : "Off";
}

/** What a lane does in this mode, in plain words. */
export function laneModeText(mode: LaneMode, reported = true): string {
  const what =
    mode === "enforce"
      ? "Blocks: Ducky's file tools refuse a write outside the lane, and the agent is told to stay inside it."
      : mode === "shadow"
        ? "Warns only: a write outside the lane still goes through and is flagged in Changes."
        : "Off: lanes are not checked at all.";
  const source = reported ? "" : " (The app did not report its lane setting; Blocks is the default.)";
  return `${what}${source} Commands run in native shells are not checked.`;
}

/** The lane cell's label: "Any file", "Read-only", or the first glob with a +N suffix. */
export function laneCellLabel(lane: string[] | null | undefined): string {
  return lane == null ? "Any file" : shortLaneLabel(lane);
}

/** Tooltip for the lane button: every glob, then what the mode does with a write outside them. */
export function laneTitle(lane: string[] | null | undefined, mode: LaneMode = "enforce", reported = true): string {
  if (lane == null) return "No write lane: this agent may write any file.";
  const configured = lane.length === 0 ? "Lane: Read-only (no file may be written)" : `Lane:\n${lane.join("\n")}`;
  return `${configured}\n${laneModeText(mode, reported)}`;
}

/** The short current-step line under a row's count. */
export function progressStep(p: SubPlanProgress): string {
  if (!p.current) return "Done";
  return p.waitingOn ? `Waiting on ${p.waitingOn}` : p.current;
}

/** "3/5 · 60%" plus the current step, for titles and details. */
export function progressText(p: SubPlanProgress): string {
  const head = `${p.title}: ${p.done}/${p.total} steps (${p.pct}%)`;
  if (!p.current) return `${head} · all done`;
  return p.waitingOn ? `${head} · next: ${p.current}, waiting on ${p.waitingOn}` : `${head} · now: ${p.current}`;
}

type Props = {
  groupId: string;
  members: GroupMemberDto[];
  /** member_conv_id of the spokesperson. The same star the sidebar draws. */
  leaderConvId?: string;
  /** Sidebar folder tree: nested groups list their agents from it until the bridge answers. */
  folders?: FolderItem[];
  allChats?: ChatTab[];
  onMembersChange: (members: GroupMemberDto[]) => void;
  onOpenMember: (chat: ChatTab) => void;
};

/** Short blurb for the invite list — ~10 words max. */
export function shortWhenToUse(text: string, maxWords = 10): string {
  const words = (text || "").trim().split(/\s+/).filter(Boolean);
  if (words.length <= maxWords) return words.join(" ");
  return `${words.slice(0, maxWords).join(" ")}…`;
}

/** Sidebar duckies that can join this group (not hubs, not already seated, not in another swarm). */
export function inviteableChats(
  allChats: ChatTab[],
  members: GroupMemberDto[],
  groupId: string,
): ChatTab[] {
  const seated = new Set(members.map((m) => m.member_conv_id).filter(Boolean));
  const gid = (groupId || "").trim();
  return allChats
    .filter((c) => {
      if (!c.id || c.id === gid) return false;
      if (c.isGroup) return false;
      if (seated.has(c.id)) return false;
      const parent = (c.parentConvId || "").trim();
      if (parent) return false;
      return true;
    })
    .slice()
    .sort((a, b) => (a.duckyName || a.name || "").localeCompare(b.duckyName || b.name || ""));
}

function isOutside(target: EventTarget | null, root: HTMLElement | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return true;
  if (root?.contains(el)) return false;
  // Model menus portal to body — a pick there is still inside the editor.
  if (el.closest?.(".dropdown-panel")) return false;
  return true;
}

/** Many agents start with their groups folded; a small team shows everyone. */
const AUTO_EXPAND_UP_TO = 12;
/** Avatars on the bar before "+N". */
const BAR_AVATARS = 12;
/** A filter box once the team is this big. */
const FILTER_FROM = 6;

const openKey = (groupId: string) => `ducky.teamRoster.open:${groupId}`;
function readOpen(groupId: string): boolean {
  try {
    return window.localStorage.getItem(openKey(groupId)) === "1";
  } catch {
    return false;
  }
}
function writeOpen(groupId: string, open: boolean): void {
  try {
    if (open) window.localStorage.setItem(openKey(groupId), "1");
    else window.localStorage.removeItem(openKey(groupId));
  } catch {
    /* a remembered pane state only */
  }
}

type Editor = { key: string; kind: "model" | "lane" | "details" | "stop" };

/** The team bar's step count; startedAt = the oldest plan's created_at (epoch s), 0 when unknown. */
type BarProgress = { done: number; total: number; pct: number; startedAt: number };
type PlanProgressApi = {
  plan_progress?: (chat_ids: string[]) => Promise<{
    ok?: boolean;
    progress?: Record<string, { done?: number; total?: number; created_at?: number; error?: string }>;
  }>;
};

/**
 * "~12 min left": time so far per finished step, times the steps left. Empty before the first
 * step finishes, when done, or without a start time.
 * ponytail: a flat average; per-step timestamps would weigh slow steps if this proves off.
 */
export function etaText(done: number, total: number, startedAt: number, nowMs = Date.now()): string {
  if (!startedAt || done <= 0 || done >= total) return "";
  const elapsedS = nowMs / 1000 - startedAt;
  if (elapsedS <= 0) return "";
  const leftMin = Math.max(1, Math.round((elapsedS / done) * (total - done) / 60));
  if (leftMin < 60) return `~${leftMin} min left`;
  const h = Math.floor(leftMin / 60);
  const m = leftMin % 60;
  return m ? `~${h} h ${m} min left` : `~${h} h left`;
}
/** Last bar value per group: a remount paints it at once instead of a blank. */
const barProgressCache = new Map<string, BarProgress>();
/** Full plans per owner chat for the open roster's rows, dropped by that chat's plan events. */
const planCache = new Map<string, ChatPlan | null>();
const PLAN_EVENTS = new Set(["plan_updated", "plan_assignment_changed"]);
const PLAN_EVENT_COALESCE_MS = 300;

/** Tests only: forget the bar and plan caches. */
export function _resetTeamProgressCacheForTests(): void {
  barProgressCache.clear();
  planCache.clear();
}

/** The one-line confirm before a whole group is stopped. */
export function stopGroupQuestion(running: number, name: string): string {
  return `Stop ${running} running agent${running === 1 ? "" : "s"} in ${name} and its sub-groups?`;
}

/**
 * A group chat's team: one compact bar (who is in, who is running or waiting, Add) and,
 * on demand, the roster under it — every agent of every nested group in one table where
 * model and write lane change in place.
 */
export function GroupMemberStrip({
  groupId,
  members,
  leaderConvId = "",
  folders = [],
  allChats = [],
  onMembersChange,
  onOpenMember,
}: Props) {
  const catalog = useDuckyCatalogOptional();
  const labelFor = catalog?.labelFor ?? ((id?: string | null) => id || "Ducky");
  const waitingIds = useWaitingChatIds();
  const runningIds = useRunningAgents();
  const [profiles, setProfiles] = useState<AgentProfileDto[]>([]);
  const [open, setOpen] = useState(() => readOpen(groupId));
  useEffect(() => setOpen(readOpen(groupId)), [groupId]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  const [query, setQuery] = useState("");
  const [laneText, setLaneText] = useState("");
  const [laneCheck, setLaneCheck] = useState<LaneCheckResult | null>(null);
  const [laneSaving, setLaneSaving] = useState(false);
  /** Journal snapshots do not establish current file ownership. */
  const [fileSnapshot, setFileSnapshot] = useState<{ groupId: string; runs: ChangesetRunDto[] | null; readAt: number } | null>(null);
  const fileRequest = useRef(0);
  const scopedFiles = fileSnapshot?.groupId === groupId ? fileSnapshot : null;
  const conflicts = conflictCountsByConv(scopedFiles?.runs || []);
  const [busy, setBusy] = useState(false);
  const [leaderId, setLeaderId] = useState(leaderConvId);
  useEffect(() => setLeaderId(leaderConvId), [leaderConvId]);
  const [error, setError] = useState("");
  /** Nested group ids the roster shows; their members come from the shared roster store. */
  const [nestedKey, setNestedKey] = useState("");
  const nestedList = useMemo(() => (nestedKey ? nestedKey.split("|") : []), [nestedKey]);
  /** Nested group id → its members from the bridge (model, lane and observation per agent). */
  const rosters = useRosters(nestedList);
  const inviteWrapRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<HTMLDivElement>(null);
  // This group's rows as the bridge last observed them: one cached roster shared with every
  // other view, refreshed by the store on roster and activity pushes (never per render).
  const ownRoster = useRoster(groupId);
  const observedMembers = useMemo(
    () => (ownRoster ? { groupId, rows: ownRoster.members } : null),
    [ownRoster, groupId],
  );
  const [observationNow, setObservationNow] = useState(Date.now());
  const refreshObservations = useCallback(() => refreshRoster(groupId), [groupId]);
  useEffect(() => setObservationNow(Date.now()), [ownRoster]);
  useEffect(() => {
    const tick = window.setInterval(() => setObservationNow(Date.now()), 15000);
    return () => window.clearInterval(tick);
  }, []);

  // Whether lanes block or only warn is an app setting, read once.
  const [laneMode, setLaneMode] = useState(() => laneModeOf(null));
  useEffect(() => {
    let live = true;
    void Promise.resolve(getApi()?.get_settings?.())
      .then((settings) => { if (live && settings) setLaneMode(laneModeOf(settings)); })
      .catch(() => undefined);
    return () => { live = false; };
  }, []);

  /** Chats that own the team's plans (where members' assignments point). */
  const planChats = useMemo(() => {
    const ids = new Set<string>();
    const add = (rows?: GroupMemberDto[]) => {
      for (const row of rows || []) {
        const owner = row.observation?.assignment?.chat_id;
        if (owner) ids.add(owner);
      }
    };
    if (observedMembers?.groupId === groupId) add(observedMembers.rows);
    for (const rows of Object.values(rosters)) add(rows);
    return [...ids].sort().join("|");
  }, [observedMembers, groupId, rosters]);
  // The bar asks right away: the leader's plan before any assignment is observed.
  const progressChats = planChats || leaderId;

  // Team bar: last value per group on first paint, then one cheap plan_progress call
  // (step counts only), again only when one of those chats' plans changes.
  const [barProgress, setBarProgress] = useState<BarProgress | null>(() => barProgressCache.get(groupId) ?? null);
  useEffect(() => setBarProgress(barProgressCache.get(groupId) ?? null), [groupId]);
  /** Bumped by a plan event for an owner chat: open rows re-read that plan. */
  const [planVersion, setPlanVersion] = useState(0);
  useEffect(() => {
    const ids = progressChats ? progressChats.split("|") : [];
    if (ids.length === 0) return;
    let live = true;
    const read = async () => {
      const call = (getApi() as unknown as PlanProgressApi | null)?.plan_progress;
      if (!call) return;
      const res = await Promise.resolve(call(ids)).catch(() => null);
      if (!live || !res?.ok) return;
      let done = 0;
      let total = 0;
      let startedAt = 0;
      for (const id of ids) {
        const p = res.progress?.[id];
        if (!p || p.error) continue;
        done += p.done || 0;
        total += p.total || 0;
        const created = Number(p.created_at) || 0;
        if (created && (!startedAt || created < startedAt)) startedAt = created;
      }
      if (!total) return;
      const next = { done, total, pct: Math.round((done / total) * 100), startedAt };
      barProgressCache.set(groupId, next);
      setBarProgress((prev) =>
        prev && prev.done === next.done && prev.total === next.total && prev.startedAt === next.startedAt ? prev : next,
      );
    };
    void read();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = subscribeAgentEvents((event) => {
      if (!PLAN_EVENTS.has(event.type) || (event.conv_id && !ids.includes(event.conv_id))) return;
      for (const id of event.conv_id ? [event.conv_id] : ids) planCache.delete(id);
      if (timer) return;
      timer = setTimeout(() => {
        timer = null;
        void read();
        setPlanVersion((n) => n + 1);
      }, PLAN_EVENT_COALESCE_MS);
    });
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
      unsubscribe();
    };
  }, [progressChats, groupId]);

  /** Full plans for the rows' own sub plan progress: read only while the roster is open, cached per chat. */
  const [plans, setPlans] = useState<Record<string, ChatPlan | null>>({});
  useEffect(() => {
    const ids = planChats ? planChats.split("|") : [];
    const api = getApi();
    if (!open || !api?.get_plan || ids.length === 0) return;
    let live = true;
    const cached = () => Object.fromEntries(ids.filter((id) => planCache.has(id)).map((id) => [id, planCache.get(id)!]));
    setPlans(cached());
    const missing = ids.filter((id) => !planCache.has(id));
    if (missing.length === 0) return;
    void Promise.all(
      missing.map(async (id) => {
        const res = await api.get_plan(id).catch(() => null);
        if (res?.ok) planCache.set(id, res.plan ?? null);
      }),
    ).then(() => { if (live) setPlans(cached()); });
    return () => { live = false; };
  }, [open, planChats, planVersion]);

  const refreshProfiles = useCallback(() => {
    const api = getApi();
    if (!api?.list_agent_profiles) return;
    void api.list_agent_profiles().then((res) => {
      setProfiles(Array.isArray(res?.profiles) ? res.profiles : []);
    });
  }, []);
  useEffect(() => {
    refreshProfiles();
  }, [refreshProfiles]);

  const profileById = useMemo(() => {
    const map = new Map<string, AgentProfileDto>();
    for (const p of profiles) if (p.id) map.set(p.id, p);
    return map;
  }, [profiles]);

  const roster = useMemo(
    () => buildRoster({ groupId, members, leaderConvId: leaderId, rosters, folders, allChats, profileById, labelFor }),
    [groupId, members, leaderId, rosters, folders, allChats, profileById, labelFor],
  );

  // Every nested group's roster, as deep as it goes, so all agents can be read and adjusted here.
  // Each id is fetched once by the store; a roster push for a nested group refreshes only that one.
  const nestedIds = useMemo(() => rosterGroupIds(roster).join("|"), [roster]);
  useEffect(() => setNestedKey(nestedIds), [nestedIds]);

  const agents = useMemo(() => rosterAgents(roster), [roster]);
  const runningCount = agents.filter((a) => runningIds.has(a.id)).length;
  const waitingCount = agents.filter((a) => waitingIds.has(a.id)).length;
  const conflictTotal = agents.reduce((sum, a) => sum + (conflicts[a.id] ?? 0), 0);
  const totalTokens = agents.reduce((sum, a) => sum + a.contextTokens, 0);
  const visible = useMemo(() => filterRoster(roster, query), [roster, query]);
  const isFolded = useCallback(
    (key: string) => (query.trim() ? false : folded[key] ?? agents.length > AUTO_EXPAND_UP_TO),
    [folded, query, agents.length],
  );

  useEffect(() => {
    if (!pickerOpen) return;
    refreshProfiles();
    const onDoc = (e: MouseEvent) => {
      if (isOutside(e.target, inviteWrapRef.current)) setPickerOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [pickerOpen, refreshProfiles]);

  useEffect(() => {
    if (!editor || editor.kind === "details") return;
    const onDoc = (e: MouseEvent) => {
      if (isOutside(e.target, editorRef.current)) setEditor(null);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [editor]);

  const nodeByKey = useMemo(() => {
    const map = new Map<string, RosterNode>();
    const walk = (list: RosterNode[]) => {
      for (const n of list) {
        map.set(n.key, n);
        walk(n.children);
      }
    };
    walk(roster);
    return map;
  }, [roster]);
  const laneNode = editor?.kind === "lane" ? nodeByKey.get(editor.key) : undefined;

  // Lane editor: instant local validation, then the server's overlap verdict (debounced).
  useEffect(() => {
    if (!laneNode) return;
    const lines = parseLaneText(laneText);
    const local = validateLaneGlobs(lines);
    if (local.length > 0) {
      setLaneCheck({ ok: false, errors: local, warnings: [], normalized: null });
      return;
    }
    const api = getApi();
    if (!api?.group_check_lane) return;
    const timer = setTimeout(() => {
      void api.group_check_lane!(laneNode.hubId, laneNode.id, lines).then(setLaneCheck).catch(() => undefined);
    }, 250);
    return () => clearTimeout(timer);
  }, [laneNode, laneText]);

  const refreshConflicts = useCallback(async () => {
    const request = ++fileRequest.current;
    let runs: ChangesetRunDto[] | null = null;
    try {
      const result = await getApi()?.list_changesets?.("", groupId, 50);
      if (Array.isArray(result)) runs = result.filter(run => run.group_id === groupId);
    } catch { /* Unavailable is not an empty journal. */ }
    if (request === fileRequest.current) setFileSnapshot({ groupId, runs, readAt: Date.now() });
  }, [groupId]);

  useEffect(() => {
    void refreshConflicts();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = subscribeAgentEvents((event) => {
      if (event.type !== "file_guard" && event.type !== "agent_stopped" && event.type !== "files_reverted") return;
      if (event.conv_id && event.conv_id !== groupId && !members.some(m => m.member_conv_id === event.conv_id)) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void refreshConflicts(), 500);
    });
    return () => {
      fileRequest.current += 1;
      if (timer) clearTimeout(timer);
      unsubscribe();
    };
  }, [refreshConflicts, groupId, members]);

  const invitedIds = useMemo(() => new Set(members.map((m) => m.profile_id).filter(Boolean)), [members]);
  const available = useMemo(
    () => profiles.filter((p) => p.id && !invitedIds.has(p.id)).slice().sort((a, b) => a.name.localeCompare(b.name)),
    [profiles, invitedIds],
  );
  const existingChats = useMemo(() => inviteableChats(allChats, members, groupId), [allChats, members, groupId]);

  /** A roster answer for this group or a nested one: keep each in its own place. */
  const applyRoster = useCallback(
    (hubId: string, next: GroupMemberDto[] | undefined, leader?: string) => {
      setRoster(hubId, next || [], leader);
      if (hubId === groupId) {
        onMembersChange(next || []);
        if (leader !== undefined) setLeaderId(leader || "");
      }
    },
    [groupId, onMembersChange],
  );

  const run = useCallback(async (work: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await work();
    } finally {
      setBusy(false);
    }
  }, []);

  const invite = useCallback(
    (profileId: string) =>
      run(async () => {
        const api = getApi();
        if (!api?.group_invite) return;
        // Profile's own model / Default Model — no per-invite override here.
        const res = await api.group_invite(groupId, profileId);
        if (!res?.ok) {
          setError(res?.error || "Invite failed");
          return;
        }
        applyRoster(groupId, res.group_members, res.leader_conv_id || undefined);
        setPickerOpen(false);
        requestReloadDuckies();
      }),
    [applyRoster, groupId, run],
  );

  const addExisting = useCallback(
    (convId: string) =>
      run(async () => {
        const api = getApi();
        if (!api?.group_add_member) return;
        const res = await api.group_add_member(groupId, convId);
        if (!res?.ok) {
          setError(res?.error || "Invite failed");
          return;
        }
        applyRoster(groupId, res.group_members, res.leader_conv_id || undefined);
        setPickerOpen(false);
        requestReloadDuckies();
      }),
    [applyRoster, groupId, run],
  );

  const createNestedGroup = useCallback(
    () =>
      run(async () => {
        const api = getApi();
        if (!api?.group_create) return;
        const parentFolder = findFolderByHubId(folders, groupId);
        const parentId = (parentFolder?.id || "").trim();
        if (!parentId) {
          setError("This group has no folder — create a nested group from the sidebar");
          return;
        }
        const name = numberedEntryName("Group", chatFolderSiblingNames(folders, parentId));
        const res = await api.group_create(name, parentId);
        if (!res?.ok || !res.id) {
          setError(res?.error || "Could not create group");
          return;
        }
        // Sync the roster so the new nested group shows up.
        const latest = await refreshRoster(groupId);
        if (latest.members.length > 0) onMembersChange(latest.members);
        setPickerOpen(false);
        requestReloadDuckies();
        onOpenMember({ id: res.id, name: (res.title || name).trim() || "Group", isGroup: true, groupMembers: res.group_members || [] });
      }),
    [folders, groupId, onMembersChange, onOpenMember, run],
  );

  const setMemberModel = useCallback(
    (node: RosterNode, model: string) =>
      run(async () => {
        const api = getApi();
        if (!api?.group_set_member_model) return;
        const res = await api.group_set_member_model(node.hubId, node.id, model);
        if (!res?.ok) {
          setError(res?.error || "Could not set model");
          return;
        }
        applyRoster(node.hubId, res.group_members);
      }),
    [applyRoster, run],
  );

  const openLaneEditor = useCallback((node: RosterNode) => {
    setEditor({ key: node.key, kind: "lane" });
    setLaneText((node.member?.write_allowed ?? []).join("\n"));
    setLaneCheck(null);
    setError("");
  }, []);

  const saveLane = useCallback(
    async (node: RosterNode, lane: string[] | null, force = false) => {
      const api = getApi();
      if (!api?.group_set_member_lane) return;
      setLaneSaving(true);
      setError("");
      try {
        const res = await api.group_set_member_lane(node.hubId, node.id, lane, force);
        if (!res?.ok) {
          setError(res?.error || "Could not set lane");
          return;
        }
        applyRoster(node.hubId, res.group_members);
        setEditor(null);
      } finally {
        setLaneSaving(false);
      }
    },
    [applyRoster],
  );

  const remove = useCallback(
    (node: RosterNode) =>
      run(async () => {
        const api = getApi();
        if (!api?.group_remove) return;
        const res = await api.group_remove(node.hubId, node.id);
        if (!res?.ok) {
          setError(res?.error || "Remove failed");
          return;
        }
        applyRoster(node.hubId, res.group_members, node.hubId === groupId ? res.leader_conv_id ?? "" : undefined);
        if (editor?.key === node.key) setEditor(null);
        requestReloadDuckies();
      }),
    [applyRoster, editor, groupId, run],
  );

  const openNode = useCallback(
    (node: RosterNode) => {
      if (node.isGroup) {
        onOpenMember({ id: node.id, name: node.name, isGroup: true });
        return;
      }
      const profile = node.member ? profileById.get(node.member.profile_id) : undefined;
      onOpenMember({
        id: node.id,
        name: node.name,
        duckyName: node.name,
        duckyStyle: node.styleId,
        duckyPersonality: profile?.ducky_personality,
        ttsVoice: node.member?.tts_voice || profile?.tts_voice,
        ttsSpeed: node.member?.tts_speed ?? profile?.tts_speed,
        model: node.model,
        codingAgent: node.codingAgent,
        parentConvId: node.hubId,
      });
    },
    [onOpenMember, profileById],
  );

  const toggleOpen = useCallback(() => {
    setOpen((was) => {
      writeOpen(groupId, !was);
      return !was;
    });
  }, [groupId]);

  /** The member row the bridge last observed (this group's or a nested group's roster). */
  const observed = (node: RosterNode): GroupMemberDto | undefined => {
    if (node.hubId === groupId) {
      return observedMembers?.groupId === groupId
        ? observedMembers.rows.find((row) => row.member_conv_id === node.id)
        : undefined;
    }
    return rosters[node.hubId]?.find((row) => row.member_conv_id === node.id);
  };

  // Mute flags set here win over the sidebar's until its next reload brings them in.
  const [mutedNow, setMutedNow] = useState<Record<string, boolean>>({});
  const isMuted = (node: RosterNode): boolean => mutedNow[node.id] ?? node.muted;
  const [stoppedNote, setStoppedNote] = useState<Record<string, string>>({});
  const runningIn = (node: RosterNode) => rosterAgents(node.children).filter((a) => runningIds.has(a.id)).length;
  const stopGroup = async (node: RosterNode) => {
    setEditor(null);
    const res = await getApi()?.group_stop?.(node.id).catch(() => null);
    if (!res?.ok) {
      setError(res?.error || `Could not stop ${node.name}`);
      return;
    }
    setStoppedNote((prev) => ({ ...prev, [node.key]: `Stopped ${res.stopped ?? 0}` }));
  };
  const toggleMute = async (node: RosterNode) => {
    const next = !isMuted(node);
    const res = await getApi()?.set_muted?.(node.id, next).catch(() => null);
    if (!res?.ok) {
      setError(res?.error || `Could not ${next ? "mute" : "unmute"} ${node.name}`);
      return;
    }
    setMutedNow((prev) => ({ ...prev, [node.id]: Boolean(res.muted ?? next) }));
  };

  /** A row's rolled-up progress, from the plans its rows' observed assignments name. */
  const progressOf = (node: RosterNode): SubPlanProgress | null =>
    rollupProgress(node, plans, (row) => observed(row)?.observation);

  const statusOf = (node: RosterNode): { label: string; cls: string } => {
    if (node.isGroup) {
      const inside = rosterAgents(node.children);
      const running = inside.filter((a) => runningIds.has(a.id)).length;
      const waiting = inside.filter((a) => waitingIds.has(a.id)).length;
      if (waiting) return { label: `${waiting} waiting`, cls: "waiting" };
      if (running) return { label: `${running} running`, cls: "running" };
      return { label: `${inside.length} ${inside.length === 1 ? "agent" : "agents"}`, cls: "idle" };
    }
    if (waitingIds.has(node.id)) return { label: "Waiting", cls: "waiting" };
    if (runningIds.has(node.id)) return { label: "Running", cls: "running" };
    return { label: "Idle", cls: "idle" };
  };

  const renderEditor = (node: RosterNode) => {
    if (!editor || editor.key !== node.key) return null;
    if (editor.kind === "stop") {
      return (
        <div className="team-roster-editor team-roster-stop-confirm" ref={editorRef} style={{ ["--depth" as string]: node.depth }}>
          <span className="team-roster-editor-label">{stopGroupQuestion(runningIn(node), node.name)}</span>
          <button type="button" className="group-member-lane-btn team-roster-stop-yes" onClick={() => void stopGroup(node)}>Stop</button>
          <button type="button" className="group-member-lane-btn" onClick={() => setEditor(null)}>Cancel</button>
        </div>
      );
    }
    if (editor.kind === "model") {
      return (
        <div className="team-roster-editor" ref={editorRef} style={{ ["--depth" as string]: node.depth }}>
          <div className="team-roster-editor-label">Model for {node.name}</div>
          <DuckyModelPicker
            model={node.model}
            onChange={(next) => void setMemberModel(node, next)}
            variant="chips"
            label=""
            hint=""
            allowClear
            placeholder="Default model"
            menuPlacement="bottom"
          />
        </div>
      );
    }
    if (editor.kind === "lane") {
      return (
        <div className="team-roster-editor" ref={editorRef} style={{ ["--depth" as string]: node.depth }}>
          <div className="team-roster-editor-label">Write lane for {node.name}</div>
          <textarea
            className="group-member-lane-textarea"
            value={laneText}
            onChange={(e) => setLaneText(e.target.value)}
            placeholder={"Content/Verse/Shop/**\nContent/Verse/module_declarations.verse"}
            spellCheck={false}
            aria-label={`Write lane for ${node.name}`}
          />
          <div className="group-member-lane-hint">
            A lane is the set of files {node.name} may write: one glob per line, a bare folder means everything in it.
            {" "}{laneModeText(laneMode.mode, laneMode.reported)} Only the group lead or you can change a lane.
            Save with no lines = read-only; Clear lane = any file.
          </div>
          {laneCheck?.errors.length ? (
            <ul className="group-member-lane-problems group-member-lane-problems--error">
              {laneCheck.errors.map((t) => <li key={t}>{t}</li>)}
            </ul>
          ) : null}
          {laneCheck?.warnings.length ? (
            <ul className="group-member-lane-problems group-member-lane-problems--warning">
              {laneCheck.warnings.map((t) => <li key={t}>{t}</li>)}
            </ul>
          ) : null}
          <div className="group-member-lane-actions">
            <button
              type="button"
              className="group-member-lane-btn group-member-lane-btn--primary"
              disabled={laneSaving || Boolean(laneCheck?.errors.length)}
              onClick={() => void saveLane(node, parseLaneText(laneText), Boolean(laneCheck?.warnings.length))}
            >
              {laneCheck?.warnings.length ? "Save anyway" : "Save"}
            </button>
            <button type="button" className="group-member-lane-btn" disabled={laneSaving} onClick={() => void saveLane(node, null)}>
              Clear lane
            </button>
            <button type="button" className="group-member-lane-btn" onClick={() => setEditor(null)}>
              Cancel
            </button>
          </div>
        </div>
      );
    }
    const member = node.member;
    const progress = progressOf(node);
    const lines = member
      ? [
          ...(progress ? [`Progress: ${progressText(progress)}`] : []),
          ...memberObservationLines({ ...member, observation: observed(node)?.observation }, node.hubId, observationNow),
          ...memberFileLines(scopedFiles?.runs ?? null, node.id, scopedFiles?.readAt ?? 0, observationNow),
        ]
      : ["Runtime: unknown", "Assignment: unknown"];
    return (
      <div className="team-roster-details" style={{ ["--depth" as string]: node.depth }}>
        {node.blurb ? <div className="team-roster-details-blurb">{shortWhenToUse(node.blurb, 24)}</div> : null}
        <div className="team-roster-details-ai">{aiTypeLabel(node.model, node.codingAgent)}</div>
        <ul className="team-roster-details-lines">
          {lines.map((line) => <li key={line}>{line}</li>)}
        </ul>
        <button
          type="button"
          className="group-member-lane-btn"
          onClick={() => { void refreshObservations(); void refreshConflicts(); for (const id of nestedList) void refreshRoster(id); }}
        >
          Refresh observation
        </button>
      </div>
    );
  };

  const renderRow = (node: RosterNode): ReactElement => {
    const status = statusOf(node);
    const waiting = !node.isGroup && waitingIds.has(node.id);
    const conflictCount = node.isGroup ? 0 : conflicts[node.id] ?? 0;
    const lane = node.member?.write_allowed;
    const laneKind = lane == null ? "none" : lane.length === 0 ? "readonly" : "set";
    const muted = isMuted(node);
    // A muted row is name + progress only: no open, no editors, a muted group hides its rows.
    const editable = Boolean(node.member) && !node.isGroup && !muted;
    const foldedNow = node.isGroup && isFolded(node.key);
    const progress = progressOf(node);
    const face = (
      <>
        <span className="team-avatar" style={{ ["--member-color" as string]: node.color || "transparent" }}>
          {node.isGroup ? (
            <span className="team-avatar-group" aria-hidden><Icons.Users /></span>
          ) : (
            <DuckyAvatar styleId={node.styleId} size={DUCKY_AVATAR_SIZES.compact} title={node.name} />
          )}
          {node.isLeader ? (
            <span className="sidebar-leader-badge" title="Group leader" aria-label="Group leader"><Icons.Star /></span>
          ) : null}
        </span>
        <span className="team-roster-name" title={node.hover || undefined}>{node.name}</span>
        {muted ? <span className="team-roster-muted-badge">MUTED</span> : null}
        {waiting ? <WaitingMarker convId={node.id} variant="chip" interactive={false} /> : null}
      </>
    );
    return (
      <Fragment key={node.key}>
        <div
          className={`team-roster-row${node.isGroup ? " team-roster-row--group" : ""}${muted ? " team-roster-row--muted" : ""}`}
          style={{ ["--depth" as string]: node.depth }}
          role="row"
        >
          <span className="team-roster-cell team-roster-cell--name" role="cell">
            {node.isGroup && !muted ? (
              <button
                type="button"
                className="team-roster-fold"
                aria-expanded={!foldedNow}
                aria-label={`${foldedNow ? "Show" : "Hide"} ${node.name}'s agents`}
                onClick={() => setFolded((prev) => ({ ...prev, [node.key]: !foldedNow }))}
              >
                {foldedNow ? <Icons.ChevronRight /> : <Icons.ChevronDown />}
              </button>
            ) : (
              <span className="team-roster-fold team-roster-fold--leaf" aria-hidden />
            )}
            {muted ? (
              <span className="team-roster-open">{face}</span>
            ) : (
              <button
                type="button"
                className="team-roster-open"
                disabled={busy}
                onClick={() => openNode(node)}
                title={node.isGroup ? `Open group ${node.name}` : `Open ${node.name}'s work`}
              >
                {face}
              </button>
            )}
          </span>
          <span className={`team-roster-cell team-roster-status team-roster-status--${status.cls}`} role="cell">
            {status.label}
          </span>
          <span
            className="team-roster-cell team-roster-plan"
            role="cell"
            title={progress ? progressText(progress) : node.isGroup ? undefined : "No assigned sub plan seen"}
          >
            {progress ? (
              <>
                <span className="team-roster-plan-top">
                  <span className="team-roster-plan-meter" aria-hidden>
                    <span style={{ ["--pct" as string]: `${progress.pct}%` }} />
                  </span>
                  <span className="team-roster-plan-count">
                    {node.isGroup ? `${progress.pct}%` : `${progress.done}/${progress.total}`}
                  </span>
                </span>
                <span className={`team-roster-plan-step${progress.waitingOn ? " team-roster-plan-step--waiting" : ""}`}>
                  {progressStep(progress)}
                </span>
              </>
            ) : node.isGroup ? null : (
              <span className="team-roster-muted">—</span>
            )}
          </span>
          <span className="team-roster-cell" role="cell">
            {editable ? (
              <button
                type="button"
                className="team-roster-pick"
                disabled={busy}
                title={`Change ${node.name}'s model`}
                onClick={() => setEditor(editor?.key === node.key && editor.kind === "model" ? null : { key: node.key, kind: "model" })}
              >
                <BillingBadge kind={billingKind(node.codingAgent, node.provider)} />
                <span className="team-roster-pick-text">{aiTypeLabel(node.model, node.codingAgent)}</span>
                <Icons.ChevronDown />
              </button>
            ) : !node.isGroup && !muted ? (
              <span className="team-roster-muted">
                <BillingBadge kind={billingKind(node.codingAgent, node.provider)} />
                {aiTypeLabel(node.model, node.codingAgent)}
              </span>
            ) : null}
          </span>
          <span className="team-roster-cell" role="cell">
            {editable ? (
              <button
                type="button"
                className={`team-roster-pick team-roster-lane team-roster-lane--${laneKind}`}
                disabled={busy}
                title={laneTitle(lane, laneMode.mode, laneMode.reported)}
                onClick={() => (editor?.key === node.key && editor.kind === "lane" ? setEditor(null) : openLaneEditor(node))}
              >
                <span className="team-roster-pick-text">{laneCellLabel(lane)}</span>
                {lane != null ? (
                  <span className={`team-roster-lane-mode team-roster-lane-mode--${laneMode.mode}`}>
                    {laneModeBadge(laneMode.mode)}
                  </span>
                ) : null}
                <Icons.ChevronDown />
              </button>
            ) : null}
          </span>
          <span className="team-roster-cell team-roster-tokens" role="cell" title="Context">
            {fmtCompactTokens(rosterTokens(node))}
            {conflictCount > 0 ? (
              <span
                className="group-member-chip-conflict"
                title={`${conflictCount} recorded file conflict${conflictCount === 1 ? "" : "s"} in runs reported running${observationNow - (scopedFiles?.readAt ?? 0) > 60000 ? " (stale snapshot)" : ""} — see Context → Files`}
              />
            ) : null}
          </span>
          <span className="team-roster-cell team-roster-actions" role="cell">
            {stoppedNote[node.key] ? <span className="team-roster-muted">{stoppedNote[node.key]}</span> : null}
            {node.isGroup ? (
              <button
                type="button"
                className="team-roster-icon team-roster-icon--stop"
                aria-label={`Stop ${node.name}`}
                title={runningIn(node) ? `Stop everything running in ${node.name} and its sub-groups` : `Nothing in ${node.name} is running`}
                disabled={busy || runningIn(node) === 0}
                onClick={() => { setStoppedNote((prev) => ({ ...prev, [node.key]: "" })); setEditor({ key: node.key, kind: "stop" }); }}
              >
                <Icons.Stop />
              </button>
            ) : null}
            {node.member ? (
              <button
                type="button"
                className="team-roster-icon team-roster-icon--mute"
                aria-label={`${muted ? "Unmute" : "Mute"} ${node.name}`}
                aria-pressed={muted}
                title={muted ? "Unmute: show this row in full again" : "Mute: collapse to a progress row (it keeps working)"}
                disabled={busy}
                onClick={() => void toggleMute(node)}
              >
                {muted ? <Icons.Maximize /> : <Icons.Minimize />}
              </button>
            ) : null}
            {!node.isGroup && !muted ? (
              <button
                type="button"
                className="team-roster-icon"
                aria-label={`Details for ${node.name}`}
                aria-expanded={editor?.key === node.key && editor.kind === "details"}
                title="Details"
                onClick={() => setEditor(editor?.key === node.key && editor.kind === "details" ? null : { key: node.key, kind: "details" })}
              >
                <Icons.MoreHorizontal />
              </button>
            ) : null}
            {node.member && !muted ? (
              <button
                type="button"
                className="team-roster-icon team-roster-icon--remove"
                aria-label={`Remove ${node.name}`}
                title={`Remove ${node.name}`}
                disabled={busy}
                onClick={() => void remove(node)}
              >
                <Icons.Close />
              </button>
            ) : null}
          </span>
        </div>
        {renderEditor(node)}
        {node.isGroup && !foldedNow && !muted ? node.children.map(renderRow) : null}
      </Fragment>
    );
  };

  // Team progress: each assigned part counted once, however many agents share it.
  const teamProgress = (() => {
    const parts = new Map<string, SubPlanProgress>();
    for (const a of agents) {
      const p = progressOf(a);
      const assignment = observed(a)?.observation?.assignment;
      if (p && assignment) parts.set(`${assignment.plan_id}:${assignment.node_id ?? ""}`, p);
    }
    let done = 0;
    let total = 0;
    for (const p of parts.values()) {
      done += p.done;
      total += p.total;
    }
    return total ? { done, total, pct: Math.round((done / total) * 100), parts: [...parts.values()] } : null;
  })();
  const barAgents = agents.slice(0, BAR_AVATARS);
  const more = agents.length - barAgents.length;
  const groupCount = rosterGroupIds(roster).length;

  return (
    <div className={`team-bar-wrap${open ? " is-open" : ""}`}>
      <div className="team-bar">
        <button
          type="button"
          className="team-bar-toggle"
          aria-expanded={open}
          aria-controls={`team-roster-${groupId}`}
          title={open ? "Hide the team" : "Show the team: model, write lane and context of every agent"}
          onClick={toggleOpen}
        >
          <span className="team-bar-icon" aria-hidden><Icons.Users /></span>
          <span className="team-bar-title">Team</span>
          <span className="team-bar-count">
            {agents.length} {agents.length === 1 ? "agent" : "agents"}
            {groupCount ? ` · ${groupCount} ${groupCount === 1 ? "group" : "groups"}` : ""}
          </span>
          {runningCount ? <span className="team-bar-stat team-bar-stat--running">{runningCount} running</span> : null}
          {waitingCount ? <span className="team-bar-stat team-bar-stat--waiting">{waitingCount} waiting</span> : null}
          {barProgress ? (
            <span
              className="team-bar-stat team-bar-stat--progress"
              title={teamProgress ? teamProgress.parts.map(progressText).join("\n") : "Team plan progress"}
            >
              {barProgress.done}/{barProgress.total} steps · {barProgress.pct}%
              {(() => {
                const eta = etaText(barProgress.done, barProgress.total, barProgress.startedAt);
                return eta ? ` · ${eta}` : "";
              })()}
            </span>
          ) : null}
          {conflictTotal ? (
            <span className="team-bar-stat team-bar-stat--conflict">
              {conflictTotal} file {conflictTotal === 1 ? "conflict" : "conflicts"}
            </span>
          ) : null}
          <span className="team-bar-chevron" aria-hidden>{open ? <Icons.ChevronDown /> : <Icons.ChevronRight />}</span>
        </button>
        <div className="team-bar-avatars" aria-label="Agents">
          {barAgents.map((a) => {
            const state = waitingIds.has(a.id) ? "waiting" : runningIds.has(a.id) ? "running" : "idle";
            const progress = progressOf(a);
            return (
              <button
                key={a.key}
                type="button"
                className={`team-bar-avatar team-bar-avatar--${state}`}
                title={`${a.name} · ${aiTypeLabel(a.model, a.codingAgent)} · ${fmtCompactTokens(a.contextTokens)}${state === "idle" ? "" : ` · ${state}`}${progress ? `\n${progressText(progress)}` : ""}`}
                aria-label={isMuted(a) ? `${a.name}, muted` : `Open ${a.name}`}
                disabled={isMuted(a)}
                onClick={() => openNode(a)}
              >
                <DuckyAvatar styleId={a.styleId} size={DUCKY_AVATAR_SIZES.compact} />
                {a.isLeader ? <span className="sidebar-leader-badge" aria-hidden><Icons.Star /></span> : null}
              </button>
            );
          })}
          {more > 0 ? (
            <button type="button" className="team-bar-more" onClick={() => { if (!open) toggleOpen(); }} title="Show every agent">
              +{more}
            </button>
          ) : null}
        </div>
        <div className="group-member-invite-wrap" ref={inviteWrapRef}>
          <button
            type="button"
            className="group-member-invite-btn"
            disabled={busy}
            onClick={() => {
              setEditor(null);
              setPickerOpen((v) => !v);
            }}
          >
            <Icons.Plus /> Add
          </button>
          {pickerOpen ? (
            <div className="group-member-picker" role="listbox">
              <button type="button" className="group-member-picker-item group-member-picker-create" disabled={busy} onClick={() => void createNestedGroup()}>
                <span className="group-member-picker-create-icon" aria-hidden><Icons.Users /></span>
                <span className="group-member-picker-meta">
                  <span className="group-member-picker-name">New group</span>
                  <span className="group-member-picker-when">Nest a subgroup here — invite duckies into it next</span>
                </span>
              </button>
              <button
                type="button"
                className="group-member-picker-item group-member-picker-create"
                disabled={busy}
                onClick={() => {
                  setPickerOpen(false);
                  requestOpenSettings("Duckies", { newDucky: true });
                }}
              >
                <span className="group-member-picker-create-icon" aria-hidden>+</span>
                <span className="group-member-picker-meta">
                  <span className="group-member-picker-name">New ducky</span>
                  <span className="group-member-picker-when">Add a ducky in Settings, then invite them here</span>
                </span>
              </button>
              {existingChats.length === 0 && available.length === 0 ? (
                <div className="group-member-picker-empty">
                  {profiles.length === 0 ? "No duckies yet — create one above" : "Everyone is already in"}
                </div>
              ) : (
                <>
                  {existingChats.map((c) => {
                    const duckyName = (c.duckyName || c.name || "").trim() || labelFor(c.duckyStyle);
                    return (
                      <button key={c.id} type="button" className="group-member-picker-item group-member-picker-invite" disabled={busy} onClick={() => void addExisting(c.id)} title={duckyName}>
                        <DuckyAvatar styleId={c.duckyStyle} size={DUCKY_AVATAR_SIZES.compact} title={duckyName} className="group-member-picker-avatar" />
                        <span className="group-member-picker-meta">
                          <span className="group-member-picker-name">{duckyName}</span>
                          <span className="group-member-picker-when">Add this ducky</span>
                        </span>
                      </button>
                    );
                  })}
                  {available.map((p) => {
                    const duckyName = (p.name || "").trim() || labelFor(p.ducky_style);
                    return (
                      <button key={p.id} type="button" className="group-member-picker-item group-member-picker-invite" disabled={busy} onClick={() => void invite(p.id)} title={p.when_to_use || p.name}>
                        <DuckyAvatar styleId={p.ducky_style} size={DUCKY_AVATAR_SIZES.compact} title={duckyName} className="group-member-picker-avatar" />
                        <span className="group-member-picker-meta">
                          <span className="group-member-picker-name">{duckyName}</span>
                          <span className="group-member-picker-when">{shortWhenToUse(p.when_to_use || p.ducky_personality || "", 10)}</span>
                        </span>
                      </button>
                    );
                  })}
                </>
              )}
            </div>
          ) : null}
        </div>
      </div>
      {open ? (
        <div className="team-roster" id={`team-roster-${groupId}`}>
          <div className="team-roster-tools">
            <span className="team-roster-summary">Total context {fmtCompactTokens(totalTokens)}</span>
            {agents.length >= FILTER_FROM ? (
              <label className="team-roster-filter">
                <Icons.Search />
                <input
                  type="search"
                  value={query}
                  placeholder="Filter by name or model"
                  aria-label="Filter agents"
                  onChange={(e) => setQuery(e.target.value)}
                />
              </label>
            ) : null}
            {groupCount ? (
              <>
                <button
                  type="button"
                  className="team-roster-tool"
                  onClick={() => setFolded(Object.fromEntries(rosterGroupIdsWithKeys(roster).map((k) => [k, false])))}
                >
                  Expand all
                </button>
                <button
                  type="button"
                  className="team-roster-tool"
                  onClick={() => setFolded(Object.fromEntries(rosterGroupIdsWithKeys(roster).map((k) => [k, true])))}
                >
                  Collapse all
                </button>
              </>
            ) : null}
          </div>
          {members.length === 0 ? (
            <div className="team-roster-empty">No one here yet. Add duckies or a nested group.</div>
          ) : (
            <div className="team-roster-table" role="table" aria-label="Team">
              <div className="team-roster-row team-roster-row--head" role="row">
                <span className="team-roster-cell" role="columnheader">Agent</span>
                <span className="team-roster-cell" role="columnheader">Status</span>
                <span className="team-roster-cell" role="columnheader">Sub plan</span>
                <span className="team-roster-cell" role="columnheader">Model</span>
                <span className="team-roster-cell" role="columnheader">Write lane</span>
                <span className="team-roster-cell team-roster-tokens" role="columnheader">Context</span>
                <span className="team-roster-cell" role="columnheader" aria-label="Actions" />
              </div>
              {visible.length === 0 ? (
                <div className="team-roster-empty">No agent matches “{query.trim()}”.</div>
              ) : (
                visible.map(renderRow)
              )}
            </div>
          )}
        </div>
      ) : null}
      {error ? <div className="group-member-strip-error" role="alert">{error}</div> : null}
    </div>
  );
}

/** Every group row's key in the tree (for Expand all / Collapse all). */
function rosterGroupIdsWithKeys(nodes: RosterNode[]): string[] {
  const out: string[] = [];
  const walk = (list: RosterNode[]) => {
    for (const n of list) {
      if (!n.isGroup) continue;
      out.push(n.key);
      walk(n.children);
    }
  };
  walk(nodes);
  return out;
}
