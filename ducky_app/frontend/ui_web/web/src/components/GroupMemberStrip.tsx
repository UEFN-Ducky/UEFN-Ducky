import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getApi } from "../hooks/usePanelApi";
import { requestOpenSettings } from "../navigation/openSettingsTab";
import type { AgentProfileDto, ChatTab, FolderItem, GroupMemberDto } from "../types/panel";
import { fmtCompactTokens } from "../utils/contextFormat";
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
  resolveNestedGroupHoverRows,
  shortModelLabel,
} from "./groupMemberHover";

export { shortModelLabel } from "./groupMemberHover";

type Props = {
  groupId: string;
  members: GroupMemberDto[];
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  /** Nested group hub id → its members (API fallback when folder tree is thin). */
  const [nestedMembers, setNestedMembers] = useState<Record<string, GroupMemberDto[]>>({});
  const inviteWrapRef = useRef<HTMLDivElement>(null);
  const modelEditWrapRef = useRef<HTMLDivElement>(null);
  const chatById = useMemo(() => new Map(allChats.map((c) => [c.id, c])), [allChats]);

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
        setPickerOpen(false);
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
        if (modelEditId === memberConvId) setModelEditId("");
      } finally {
        setBusy(false);
      }
    },
    [groupId, modelEditId, onMembersChange],
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
            return (
              <div
                key={m.member_conv_id}
                className="group-member-chip-wrap"
                ref={editing ? modelEditWrapRef : undefined}
              >
                <EditorTabHoverCardShell
                  placement="below"
                  disabled={editing}
                  cardHeight={
                    nestedGroup
                      ? Math.min(360, 88 + Math.max(1, nestedRoster.length) * 26 + 48)
                      : Math.min(280, 120 + (blurb ? 48 : 0) + (contextTokens > 0 ? 36 : 0))
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
                        {nestedGroup
                          ? "Click → open subgroup · one rep speaks here"
                          : "Click name → their work · model badge → change LLM"}
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
              {available.length === 0 ? (
                <div className="group-member-picker-empty">
                  {profiles.length === 0
                    ? "No duckies yet — create one above"
                    : "Everyone is already in"}
                </div>
              ) : (
                available.map((p) => {
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
                })
              )}
            </div>
          ) : null}
        </div>
      </div>
      {error ? <div className="group-member-strip-error">{error}</div> : null}
    </div>
  );
}
