import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { subscribeAgentEvents } from "../hooks/useAgentEventBus";
import { requestReloadDuckies } from "../hooks/useChatsChanged";
import { getApi } from "../hooks/usePanelApi";
import { requestOpenSettings } from "../navigation/openSettingsTab";
import type { AgentProfileDto, ChatTab, ChangesetRunDto, FolderItem, GroupMemberDto, LaneCheckResult } from "../types/panel";
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
import { modelFromFavorites } from "./ducky/duckyProfileForm";
import { EditorTabHoverCardShell } from "./editor/EditorTabHoverCardShell";
import {
  aiTypeLabel,
  memberObservationLines,
  memberFileLines,
  resolveNestedGroupHoverRows,
  shortModelLabel,
} from "./groupMemberHover";

export { shortModelLabel } from "./groupMemberHover";
export { parseLaneText, shortLaneLabel, validateLaneGlobs } from "../utils/laneGlob";

/** Tooltip for the lane badge. */
export function laneTitle(lane: string[] | null | undefined): string {
  const configured = lane == null ? "No write lane configured" : lane.length === 0
    ? "Configured lane: Read-only" : `Configured lane:\n${lane.join("\n")}`;
  return `${configured}\nCurrent enforcement coverage: unknown; native shells are not guaranteed to honor this lane.`;
}

type Props = {
  groupId: string;
  members: GroupMemberDto[];
  /** member_conv_id of the spokesperson. The same star the sidebar draws. */
  leaderConvId?: string;
  /** Sidebar folder tree — nested group hovers list every agent + LLM + context. */
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

/** Library profile title (Verse Coder) — never avatar style (Artist). */
function memberDuckyName(
  m: GroupMemberDto,
  profile: AgentProfileDto | undefined,
  labelFor: (styleId?: string | null) => string,
): string {
  const fromProfile = (profile?.name || "").trim();
  if (fromProfile) return fromProfile;
  const fromRole = (m.name || "").trim();
  if (fromRole) return fromRole;
  const fromMember = (m.ducky_name || "").trim();
  if (fromMember) return fromMember;
  return labelFor(m.ducky_style || profile?.ducky_style);
}

function isOutsidePicker(target: EventTarget | null, root: HTMLElement | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return true;
  if (root?.contains(el)) return false;
  // ModelSelector menus portal to body — keep the member model popover open.
  if (el.closest?.(".dropdown-panel")) return false;
  return true;
}

/**
 * Header strip for group chats: who's in the room + invite/remove.
 * Invite = pick only. Change LLM on members already in the chat (model badge).
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
  const [profiles, setProfiles] = useState<AgentProfileDto[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [modelEditId, setModelEditId] = useState("");
  const [laneEditId, setLaneEditId] = useState("");
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
  /** Nested group hub id → its members (API fallback when folder tree is thin). */
  const [nestedMembers, setNestedMembers] = useState<Record<string, GroupMemberDto[]>>({});
  const inviteWrapRef = useRef<HTMLDivElement>(null);
  const modelEditWrapRef = useRef<HTMLDivElement>(null);
  const laneEditWrapRef = useRef<HTMLDivElement>(null);
  const chatById = useMemo(() => new Map(allChats.map((c) => [c.id, c])), [allChats]);
  const [observedMembers, setObservedMembers] = useState<{ groupId: string; rows: GroupMemberDto[] } | null>(null);
  const [observationNow, setObservationNow] = useState(Date.now());
  const observationRequest = useRef(0);
  const refreshObservations = useCallback(async () => {
    const request = ++observationRequest.current;
    try {
      const result = await getApi()?.group_members?.(groupId);
      if (request !== observationRequest.current) return;
      setObservedMembers({ groupId, rows: result?.ok ? result.members || [] : [] });
    } catch {
      if (request === observationRequest.current) setObservedMembers({ groupId, rows: [] });
    }
    setObservationNow(Date.now());
  }, [groupId]);
  useEffect(() => {
    void refreshObservations();
    const tick = window.setInterval(() => setObservationNow(Date.now()), 15000);
    return () => { observationRequest.current += 1; window.clearInterval(tick); };
  }, [refreshObservations, members]);
  useEffect(() => subscribeAgentEvents(event => {
    const ids = new Set([groupId, ...members.map(m => m.member_conv_id)]);
    if (observedMembers?.groupId === groupId) {
      for (const row of observedMembers.rows) {
        const owner = row.observation?.assignment?.chat_id;
        if (owner) ids.add(owner);
      }
    }
    if (event.conv_id && ids.has(event.conv_id) &&
        ["plan_updated", "plan_assignment_changed", "agent_started", "agent_stopped"].includes(event.type)) {
      void refreshObservations();
    }
  }), [groupId, members, observedMembers, refreshObservations]);

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

  // Prefetch nested group rosters so hover can show each member's LLM.
  useEffect(() => {
    const api = getApi();
    if (!api?.group_members) return;
    const nestedIds = members.filter((m) => m.is_group).map((m) => m.member_conv_id);
    if (nestedIds.length === 0) {
      setNestedMembers({});
      return;
    }
    let cancelled = false;
    void Promise.all(
      nestedIds.map(async (id) => {
        const res = await api.group_members!(id);
        return [id, res?.ok ? res.members || [] : []] as const;
      }),
    ).then((rows) => {
      if (cancelled) return;
      const next: Record<string, GroupMemberDto[]> = {};
      for (const [id, list] of rows) next[id] = list;
      setNestedMembers(next);
    });
    return () => {
      cancelled = true;
    };
  }, [members]);

  useEffect(() => {
    if (!pickerOpen) return;
    refreshProfiles();
    const onDoc = (e: MouseEvent) => {
      if (isOutsidePicker(e.target, inviteWrapRef.current)) setPickerOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [pickerOpen, refreshProfiles]);

  useEffect(() => {
    if (!modelEditId) return;
    const onDoc = (e: MouseEvent) => {
      if (isOutsidePicker(e.target, modelEditWrapRef.current)) setModelEditId("");
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [modelEditId]);

  useEffect(() => {
    if (!laneEditId) return;
    const onDoc = (e: MouseEvent) => {
      if (isOutsidePicker(e.target, laneEditWrapRef.current)) setLaneEditId("");
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [laneEditId]);

  // Lane editor: instant local validation, then the server's overlap verdict (debounced).
  useEffect(() => {
    if (!laneEditId) return;
    const lines = parseLaneText(laneText);
    const local = validateLaneGlobs(lines);
    if (local.length > 0) {
      setLaneCheck({ ok: false, errors: local, warnings: [], normalized: null });
      return;
    }
    const api = getApi();
    if (!api?.group_check_lane) return;
    const timer = setTimeout(() => {
      void api.group_check_lane!(groupId, laneEditId, lines).then(setLaneCheck).catch(() => undefined);
    }, 250);
    return () => clearTimeout(timer);
  }, [groupId, laneEditId, laneText]);

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

  const profileById = useMemo(() => {
    const map = new Map<string, AgentProfileDto>();
    for (const p of profiles) {
      if (p.id) map.set(p.id, p);
    }
    return map;
  }, [profiles]);

  const invitedIds = useMemo(
    () => new Set(members.map((m) => m.profile_id).filter(Boolean)),
    [members],
  );

  const available = useMemo(
    () =>
      profiles
        .filter((p) => p.id && !invitedIds.has(p.id))
        .slice()
        .sort((a, b) => a.name.localeCompare(b.name)),
    [profiles, invitedIds],
  );

  const existingChats = useMemo(
    () => inviteableChats(allChats, members, groupId),
    [allChats, members, groupId],
  );

  const invite = useCallback(
    async (profileId: string) => {
      const api = getApi();
      if (!api?.group_invite) return;
      setBusy(true);
      setError("");
      try {
        // Profile's own model / Default Model — no per-invite override here.
        const res = await api.group_invite(groupId, profileId);
        if (!res?.ok) {
          setError(res?.error || "Invite failed");
          return;
        }
        onMembersChange(res.group_members || []);
        if (res.leader_conv_id) setLeaderId(res.leader_conv_id);
        setPickerOpen(false);
        requestReloadDuckies();
      } finally {
        setBusy(false);
      }
    },
    [groupId, onMembersChange],
  );

  const addExisting = useCallback(
    async (convId: string) => {
      const api = getApi();
      if (!api?.group_add_member) return;
      setBusy(true);
      setError("");
      try {
        const res = await api.group_add_member(groupId, convId);
        if (!res?.ok) {
          setError(res?.error || "Invite failed");
          return;
        }
        onMembersChange(res.group_members || []);
        if (res.leader_conv_id) setLeaderId(res.leader_conv_id);
        setPickerOpen(false);
        requestReloadDuckies();
      } finally {
        setBusy(false);
      }
    },
    [groupId, onMembersChange],
  );

  const createNestedGroup = useCallback(async () => {
    const api = getApi();
    if (!api?.group_create) return;
    const parentFolder = findFolderByHubId(folders, groupId);
    const parentId = (parentFolder?.id || "").trim();
    if (!parentId) {
      setError("This group has no folder — create a nested group from the sidebar");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const siblings = chatFolderSiblingNames(folders, parentId);
      const name = numberedEntryName("Group", siblings);
      const res = await api.group_create(name, parentId);
      if (!res?.ok || !res.id) {
        setError(res?.error || "Could not create group");
        return;
      }
      // Sync parent roster so the new nested hub shows up as a chip.
      const roster = api.group_members ? await api.group_members(groupId) : null;
      if (roster?.ok) onMembersChange(roster.members || []);
      setPickerOpen(false);
      requestReloadDuckies();
      onOpenMember({
        id: res.id,
        name: (res.title || name).trim() || "Group",
        isGroup: true,
        groupMembers: res.group_members || [],
      });
    } finally {
      setBusy(false);
    }
  }, [folders, groupId, onMembersChange, onOpenMember]);

  const setMemberModel = useCallback(
    async (memberConvId: string, model: string) => {
      const api = getApi();
      if (!api?.group_set_member_model) return;
      setBusy(true);
      setError("");
      try {
        const res = await api.group_set_member_model(groupId, memberConvId, model);
        if (!res?.ok) {
          setError(res?.error || "Could not set model");
          return;
        }
        onMembersChange(res.group_members || []);
      } finally {
        setBusy(false);
      }
    },
    [groupId, onMembersChange],
  );

  const openLaneEditor = useCallback((m: GroupMemberDto) => {
    setModelEditId("");
    setLaneEditId(m.member_conv_id);
    setLaneText((m.write_allowed ?? []).join("\n"));
    setLaneCheck(null);
    setError("");
  }, []);

  const saveLane = useCallback(
    async (memberConvId: string, lane: string[] | null, force = false) => {
      const api = getApi();
      if (!api?.group_set_member_lane) return;
      setLaneSaving(true);
      setError("");
      try {
        const res = await api.group_set_member_lane(groupId, memberConvId, lane, force);
        if (!res?.ok) {
          setError(res?.error || "Could not set lane");
          return;
        }
        onMembersChange(res.group_members || []);
        setLaneEditId("");
      } finally {
        setLaneSaving(false);
      }
    },
    [groupId, onMembersChange],
  );

  const remove = useCallback(
    async (memberConvId: string) => {
      const api = getApi();
      if (!api?.group_remove) return;
      setBusy(true);
      setError("");
      try {
        const res = await api.group_remove(groupId, memberConvId);
        if (!res?.ok) {
          setError(res?.error || "Remove failed");
          return;
        }
        onMembersChange(res.group_members || []);
        if (res.leader_conv_id !== undefined) setLeaderId(res.leader_conv_id || "");
        if (modelEditId === memberConvId) setModelEditId("");
        if (laneEditId === memberConvId) setLaneEditId("");
        requestReloadDuckies();
      } finally {
        setBusy(false);
      }
    },
    [groupId, laneEditId, modelEditId, onMembersChange],
  );

  const openMember = useCallback(
    (m: GroupMemberDto) => {
      if (m.is_group) {
        onOpenMember({
          id: m.member_conv_id,
          name: (m.name || m.ducky_name || "Group").trim() || "Group",
          isGroup: true,
        });
        return;
      }
      const profile = profileById.get(m.profile_id);
      const styleId = m.ducky_style || profile?.ducky_style;
      const duckyName = memberDuckyName(m, profile, labelFor);
      onOpenMember({
        id: m.member_conv_id,
        name: duckyName,
        duckyName,
        duckyStyle: styleId,
        duckyPersonality: profile?.ducky_personality,
        ttsVoice: m.tts_voice || profile?.tts_voice,
        ttsSpeed: m.tts_speed ?? profile?.tts_speed,
        model: m.model,
        codingAgent: m.coding_agent,
        parentConvId: groupId,
      });
    },
    [groupId, labelFor, onOpenMember, profileById],
  );

  return (
    <div className="group-member-strip">
      <div className="group-member-strip-label">In this chat</div>
      <div className="group-member-strip-chips">
        {members.length === 0 ? (
          <span className="group-member-strip-empty">Invite duckies to join</span>
        ) : (
          members.map((m) => {
            const nestedGroup = Boolean(m.is_group);
            const profile = profileById.get(m.profile_id);
            const styleId = m.ducky_style || profile?.ducky_style;
            const duckyName = nestedGroup
              ? (m.name || m.ducky_name || "Group").trim() || "Group"
              : memberDuckyName(m, profile, labelFor);
            const styleLabel = nestedGroup ? "" : labelFor(styleId);
            const memberChat = chatById.get(m.member_conv_id);
            const model =
              (m.model || "").trim() ||
              (memberChat?.model || "").trim() ||
              modelFromFavorites(profile?.favorite_models);
            const codingAgent =
              (m.coding_agent || "").trim() ||
              (memberChat?.codingAgent || "").trim() ||
              "ducky";
            const contextTokens = Math.max(0, Number(memberChat?.contextTokens) || 0);
            const nestedRoster = nestedGroup
              ? resolveNestedGroupHoverRows(
                  m.member_conv_id,
                  folders,
                  allChats,
                  nestedMembers[m.member_conv_id] || [],
                  profileById,
                  labelFor,
                )
              : [];
            const nestedTotal = nestedRoster.reduce((sum, r) => sum + r.contextTokens, 0);
            const blurb = nestedGroup
              ? nestedRoster.length > 0
                ? undefined
                : "One representative answers for this group"
              : shortWhenToUse(
                  profile?.when_to_use ||
                    profile?.ducky_personality ||
                    memberChat?.duckyPersonality ||
                    "",
                  18,
                );
            const editing = !nestedGroup && modelEditId === m.member_conv_id;
            const laneEditing = !nestedGroup && laneEditId === m.member_conv_id;
            const conflictCount = conflicts[m.member_conv_id] ?? 0;
            const laneKind = m.write_allowed == null ? "none" : m.write_allowed.length === 0 ? "readonly" : "set";
            return (
              <div
                key={m.member_conv_id}
                className={`group-member-chip-wrap${nestedGroup ? " group-member-chip-wrap--nested" : ""}`}
                ref={editing ? modelEditWrapRef : laneEditing ? laneEditWrapRef : undefined}
              >
                <EditorTabHoverCardShell
                  placement="below"
                  disabled={editing || laneEditing}
                  cardHeight={
                    nestedGroup
                      ? Math.min(520, 248 + Math.max(1, nestedRoster.length) * 26 + 48)
                      : Math.min(620, 460 + (blurb ? 48 : 0) + (contextTokens > 0 ? 36 : 0))
                  }
                  card={
                    <>
                      <div className="editor-tab-hover-card-header">
                        {nestedGroup ? (
                          <span className="group-member-nested-icon" aria-hidden>
                            <Icons.Users />
                          </span>
                        ) : (
                          <DuckyAvatar styleId={styleId} size={44} />
                        )}
                        <div className="editor-tab-hover-card-titles">
                          <div className="editor-tab-hover-card-name">{duckyName}</div>
                          {nestedGroup ? (
                            <div className="editor-tab-hover-card-subtitle">
                              {nestedRoster.length}{" "}
                              {nestedRoster.length === 1 ? "agent" : "agents"}
                            </div>
                          ) : styleLabel &&
                            styleLabel.toLowerCase() !== duckyName.toLowerCase() ? (
                            <div className="editor-tab-hover-card-subtitle">{styleLabel}</div>
                          ) : null}
                        </div>
                      </div>
                      {!nestedGroup ? (
                        <div className="editor-tab-hover-card-meta">
                          <span className="editor-tab-hover-card-model">
                            {aiTypeLabel(model, codingAgent)}
                          </span>
                        </div>
                      ) : null}
                      {!nestedGroup && m.write_allowed != null ? (
                        <div className="editor-tab-hover-card-lane">
                          Configured lane: {m.write_allowed.length > 0 ? m.write_allowed.join(", ") : "read-only"}
                        </div>
                      ) : null}
                      {nestedGroup && nestedRoster.length > 0 ? (
                        <div className="editor-tab-hover-card-folder-list">
                          {nestedRoster.map((row) => (
                            <div key={row.id} className="editor-tab-hover-card-folder-row">
                              <span className="editor-tab-hover-card-folder-row-avatar">
                                {row.isGroup ? (
                                  <span
                                    className="editor-tab-hover-card-folder-row-icon"
                                    aria-hidden
                                  >
                                    <Icons.Users />
                                  </span>
                                ) : (
                                  <DuckyAvatar
                                    styleId={row.duckyStyle}
                                    size={18}
                                    title={row.name}
                                  />
                                )}
                              </span>
                              <span
                                className="editor-tab-hover-card-folder-row-name"
                                title={row.name}
                              >
                                {row.name}
                              </span>
                              <span
                                className="editor-tab-hover-card-folder-row-model"
                                title={aiTypeLabel(row.model, row.codingAgent)}
                              >
                                {aiTypeLabel(row.model, row.codingAgent)}
                              </span>
                              <span className="editor-tab-hover-card-folder-row-tokens">
                                {fmtCompactTokens(row.contextTokens)}
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : null}
                      {blurb ? <div className="editor-tab-hover-card-personality">{blurb}</div> : null}
                      {nestedGroup && nestedRoster.length > 0 ? (
                        <div className="editor-tab-hover-card-folder-total">
                          <span>Total context</span>
                          <span className="editor-tab-hover-card-folder-total-value">
                            {fmtCompactTokens(nestedTotal)} tokens
                          </span>
                        </div>
                      ) : null}
                      {!nestedGroup && contextTokens > 0 ? (
                        <div className="editor-tab-hover-card-folder-total">
                          <span>Context</span>
                          <span className="editor-tab-hover-card-folder-total-value">
                            {fmtCompactTokens(contextTokens)} tokens
                          </span>
                        </div>
                      ) : null}
                      <div className="editor-tab-hover-card-status">
                        {memberObservationLines({ ...m, observation: observedMembers?.groupId === groupId
                          ? observedMembers.rows.find(row => row.member_conv_id === m.member_conv_id)?.observation
                          : undefined }, groupId, observationNow).map(line => <div key={line}>{line}</div>)}
                        {memberFileLines(scopedFiles?.runs ?? null, m.member_conv_id, scopedFiles?.readAt ?? 0, observationNow).map(line => <div key={line}>{line}</div>)}
                        <button type="button" onClick={() => { void refreshObservations(); void refreshConflicts(); }}>Refresh observation</button>
                        {nestedGroup
                          ? "Click → open subgroup · one rep speaks here"
                          : "Click name → their work · model badge → LLM · lane badge → write lane"}
                      </div>
                    </>
                  }
                >
                  <span
                    className={`group-member-chip${nestedGroup ? " group-member-chip--nested-group" : ""}`}
                    style={{ ["--member-color" as string]: m.color || "#7aa2f7" }}
                  >
                    <button
                      type="button"
                      className="group-member-chip-main"
                      disabled={busy}
                      onClick={() => openMember(m)}
                      title={nestedGroup ? `Open group ${duckyName}` : `Open ${duckyName}'s work`}
                    >
                      <span className="group-member-chip-avatar-wrap">
                      {nestedGroup ? (
                        <span className="group-member-chip-avatar group-member-chip-avatar--group" aria-hidden>
                          <Icons.Users />
                        </span>
                      ) : (
                        <DuckyAvatar
                          styleId={styleId}
                          size={DUCKY_AVATAR_SIZES.compact}
                          title={duckyName}
                          className="group-member-chip-avatar"
                        />
                      )}
                      {!nestedGroup && leaderId && m.member_conv_id === leaderId ? (
                        <span className="sidebar-leader-badge" title="Group leader" aria-label="Group leader">
                          <Icons.Star />
                        </span>
                      ) : null}
                      </span>
                      <span className="group-member-chip-name">{duckyName}</span>
                    </button>
                    {!nestedGroup ? (
                      <button
                        type="button"
                        className="group-member-chip-model"
                        disabled={busy}
                        title="Change model"
                        onClick={() => setModelEditId(editing ? "" : m.member_conv_id)}
                      >
                        {shortModelLabel(model)}
                      </button>
                    ) : null}
                    {!nestedGroup ? (
                      <button
                        type="button"
                        className={`group-member-chip-lane group-member-chip-lane--${laneKind}`}
                        disabled={busy}
                        title={laneTitle(m.write_allowed)}
                        onClick={() => (laneEditing ? setLaneEditId("") : openLaneEditor(m))}
                      >
                        Configured: {shortLaneLabel(m.write_allowed)}
                      </button>
                    ) : null}
                    {!nestedGroup ? (
                      <span className="group-member-chip-tokens" title="Context">
                        {fmtCompactTokens(contextTokens)}
                      </span>
                    ) : null}
                    {conflictCount > 0 ? (
                      <span
                        className="group-member-chip-conflict"
                        title={`${conflictCount} recorded file conflict${conflictCount === 1 ? "" : "s"} in runs reported running${observationNow - (scopedFiles?.readAt ?? 0) > 60000 ? " (stale snapshot)" : ""} — see Context → Files`}
                      />
                    ) : null}
                    <button
                      type="button"
                      className="group-member-chip-remove"
                      title={`Remove ${duckyName}`}
                      disabled={busy}
                      onClick={() => void remove(m.member_conv_id)}
                    >
                      ×
                    </button>
                  </span>
                </EditorTabHoverCardShell>
                {nestedGroup ? (
                  <div className="group-member-nested-roster" aria-label={`${duckyName} agents`}>
                    {nestedRoster.map((row) => (
                      <span key={row.id} className="group-member-nested-roster-row">
                        <span className="group-member-nested-roster-name">{row.name}</span>
                        <span className="group-member-nested-roster-tokens" title="Context">
                          {fmtCompactTokens(row.contextTokens)}
                        </span>
                      </span>
                    ))}
                  </div>
                ) : null}
                {editing ? (
                  <div className="group-member-model-popover">
                    <div className="group-member-model-popover-label">Model for {duckyName}</div>
                    <DuckyModelPicker
                      model={model}
                      onChange={(next) => void setMemberModel(m.member_conv_id, next)}
                      variant="chips"
                      label=""
                      hint=""
                      allowClear
                      placeholder="Default model"
                      menuPlacement="bottom"
                    />
                  </div>
                ) : null}
                {laneEditing ? (
                  <div className="group-member-model-popover group-member-lane-popover">
                    <div className="group-member-model-popover-label">Write lane for {duckyName}</div>
                    <textarea
                      className="group-member-lane-textarea"
                      value={laneText}
                      onChange={(e) => setLaneText(e.target.value)}
                      placeholder={"Content/Verse/Shop/**\nContent/Verse/module_declarations.verse"}
                      spellCheck={false}
                    />
                    <div className="group-member-lane-hint">
                      One glob per line; a bare folder means folder/**. Save with no lines = configured read-only. Clear = no configured restriction. Enforcement coverage is unknown; native shells may bypass lanes.
                    </div>
                    {laneCheck?.errors.length ? (
                      <ul className="group-member-lane-problems group-member-lane-problems--error">
                        {laneCheck.errors.map((t) => (
                          <li key={t}>{t}</li>
                        ))}
                      </ul>
                    ) : null}
                    {laneCheck?.warnings.length ? (
                      <ul className="group-member-lane-problems group-member-lane-problems--warning">
                        {laneCheck.warnings.map((t) => (
                          <li key={t}>{t}</li>
                        ))}
                      </ul>
                    ) : null}
                    <div className="group-member-lane-actions">
                      <button
                        type="button"
                        className="group-member-lane-btn group-member-lane-btn--primary"
                        disabled={laneSaving || Boolean(laneCheck?.errors.length)}
                        onClick={() =>
                          void saveLane(m.member_conv_id, parseLaneText(laneText), Boolean(laneCheck?.warnings.length))
                        }
                      >
                        {laneCheck?.warnings.length ? "Save anyway" : "Save"}
                      </button>
                      <button
                        type="button"
                        className="group-member-lane-btn"
                        disabled={laneSaving}
                        onClick={() => void saveLane(m.member_conv_id, null)}
                      >
                        Clear lane
                      </button>
                      <button type="button" className="group-member-lane-btn" onClick={() => setLaneEditId("")}>
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
            );
          })
        )}
        <div className="group-member-invite-wrap" ref={inviteWrapRef}>
          <button
            type="button"
            className="group-member-invite-btn"
            disabled={busy}
            onClick={() => {
              setModelEditId("");
              setPickerOpen((v) => !v);
            }}
          >
            <Icons.Users /> Invite
          </button>
          {pickerOpen ? (
            <div className="group-member-picker" role="listbox">
              <button
                type="button"
                className="group-member-picker-item group-member-picker-create"
                disabled={busy}
                onClick={() => void createNestedGroup()}
              >
                <span className="group-member-picker-create-icon" aria-hidden>
                  <Icons.Users />
                </span>
                <span className="group-member-picker-meta">
                  <span className="group-member-picker-name">New group</span>
                  <span className="group-member-picker-when">
                    Nest a subgroup here — invite duckies into it next
                  </span>
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
                <span className="group-member-picker-create-icon" aria-hidden>
                  +
                </span>
                <span className="group-member-picker-meta">
                  <span className="group-member-picker-name">New ducky</span>
                  <span className="group-member-picker-when">
                    Add a ducky in Settings, then invite them here
                  </span>
                </span>
              </button>
              {existingChats.length === 0 && available.length === 0 ? (
                <div className="group-member-picker-empty">
                  {profiles.length === 0
                    ? "No duckies yet — create one above"
                    : "Everyone is already in"}
                </div>
              ) : (
                <>
                  {existingChats.map((c) => {
                    const duckyName = (c.duckyName || c.name || "").trim() || labelFor(c.duckyStyle);
                    return (
                      <button
                        key={c.id}
                        type="button"
                        className="group-member-picker-item group-member-picker-invite"
                        disabled={busy}
                        onClick={() => void addExisting(c.id)}
                        title={duckyName}
                      >
                        <DuckyAvatar
                          styleId={c.duckyStyle}
                          size={DUCKY_AVATAR_SIZES.compact}
                          title={duckyName}
                          className="group-member-picker-avatar"
                        />
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
                      <button
                        key={p.id}
                        type="button"
                        className="group-member-picker-item group-member-picker-invite"
                        disabled={busy}
                        onClick={() => void invite(p.id)}
                        title={p.when_to_use || p.name}
                      >
                        <DuckyAvatar
                          styleId={p.ducky_style}
                          size={DUCKY_AVATAR_SIZES.compact}
                          title={duckyName}
                          className="group-member-picker-avatar"
                        />
                        <span className="group-member-picker-meta">
                          <span className="group-member-picker-name">{duckyName}</span>
                          <span className="group-member-picker-when">
                            {shortWhenToUse(p.when_to_use || p.ducky_personality || "", 10)}
                          </span>
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
      {error ? <div className="group-member-strip-error">{error}</div> : null}
    </div>
  );
}
