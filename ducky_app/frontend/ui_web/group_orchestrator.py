"""Multi-ducky group chat: route → member turn → @mention hops → shared transcript."""

from __future__ import annotations

import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from frontend.ui_web.project_chats import append_message, auto_title, load_conversation, save_conversation

PushFn = Callable[[dict[str, Any]], None]

# ponytail: hard hop budget stops runaway @mention ping-pong; raise if roundtables
# need longer teammate chains, or replace with a cheap "should continue?" classifier.
_MENTION_HOP_BUDGET = 4
_TRANSCRIPT_LINES = 10
_MEMBER_COLORS = (
    "#7aa2f7",
    "#9ece6a",
    "#e0af68",
    "#bb9af7",
    "#f7768e",
    "#7dcfff",
    "#ff9e64",
    "#c0caf5",
)

# Presence / roll-call → every member speaks (not one router pick).
_ROUNDTABLE_RE = re.compile(
    r"(?i)\b("
    r"who'?s?\s+(in|here|present|around|this)|"
    r"who\s+are\s+(you|here)|"
    r"introduce\s+(yourself|yourselves)|"
    r"say\s+(hi|hello)|"
    r"everyone|everybody|all\s+of\s+you|roll\s*call|"
    r"what('?s| is)\s+everyone"
    r")\b"
)

def member_color_for_index(index: int) -> str:
    return _MEMBER_COLORS[index % len(_MEMBER_COLORS)]


def is_group_conversation(conv: Any) -> bool:
    return bool(getattr(conv, "is_group", False))


def normalize_member(raw: dict[str, Any], *, index: int = 0) -> dict[str, Any]:
    return {
        "member_conv_id": str(raw.get("member_conv_id") or "").strip(),
        "profile_id": str(raw.get("profile_id") or "").strip(),
        # Role / profile handle (unique for @mentions).
        "name": str(raw.get("name") or "").strip() or "Ducky",
        # Duck identity shown in the strip / bubbles.
        "ducky_name": str(raw.get("ducky_name") or "").strip(),
        "ducky_style": str(raw.get("ducky_style") or "").strip(),
        # Qualified "backend:model" for strip pickers (optional).
        "model": str(raw.get("model") or "").strip(),
        "coding_agent": str(raw.get("coding_agent") or "").strip(),
        "tts_voice": str(raw.get("tts_voice") or "").strip(),
        "tts_speed": float(raw.get("tts_speed") or 0.0),
        "color": str(raw.get("color") or "").strip() or member_color_for_index(index),
        # Nested group hub — one representative speaks for the whole subgroup.
        "is_group": bool(raw.get("is_group")),
    }


def group_members(conv: Any) -> list[dict[str, Any]]:
    raw = getattr(conv, "group_members", None) or []
    out: list[dict[str, Any]] = []
    for i, row in enumerate(raw):
        if isinstance(row, dict):
            out.append(normalize_member(row, index=i))
    return out


def is_subagent_conversation(conv: Any, project_root: str | None = None) -> bool:
    """True when parent is a normal ducky (not a group hub) — AI-spawned only."""
    parent_id = (getattr(conv, "parent_conv_id", None) or "").strip()
    if not parent_id:
        return False
    parent = load_conversation(parent_id, project_root=project_root)
    if parent is None:
        return False
    return not is_group_conversation(parent)


_NESTED_GROUP_DEPTH = 3


def _group_folder_id(group_id: str, project_root: str | None = None) -> str:
    from frontend.ui_web.project_chats import load_folders

    gid = (group_id or "").strip()
    if not gid:
        return ""
    for folder in load_folders(project_root):
        if (getattr(folder, "group_hub_id", None) or "").strip() == gid:
            return folder.id
    conv = load_conversation(gid, project_root=project_root)
    return (getattr(conv, "folder_id", None) or "").strip() if conv else ""


def sync_group_members_from_folder(group: Any, project_root: str | None = None) -> list[dict[str, Any]]:
    """Rebuild group_members from chats + nested group folders inside the group folder."""
    from frontend.ui_web.project_chats import list_conversations, load_folders

    if not group or not is_group_conversation(group):
        return []
    group_id = str(group.id)
    folder_id = _group_folder_id(group_id, project_root)
    if not folder_id:
        return group_members(group)

    folders = load_folders(project_root)
    all_convs = list_conversations(project_root=project_root)
    by_id = {c.id: c for c in all_convs}
    existing = {
        m["member_conv_id"]: m
        for m in group_members(group)
        if m.get("member_conv_id")
    }
    new_rows: list[dict[str, Any]] = []

    for conv in all_convs:
        if (getattr(conv, "folder_id", None) or "") != folder_id:
            continue
        if conv.id == group_id or getattr(conv, "is_group", False):
            continue
        # Subagents nest under a non-group parent — never group members.
        parent_id = (getattr(conv, "parent_conv_id", None) or "").strip()
        if parent_id:
            parent = by_id.get(parent_id)
            if parent is not None and not is_group_conversation(parent):
                continue
        prev = existing.get(conv.id) or {}
        name = (
            str(prev.get("name") or "").strip()
            or str(getattr(conv, "ducky_name", None) or "").strip()
            or str(conv.title or "").strip()
            or "Ducky"
        )
        row = normalize_member(
            {
                **prev,
                "member_conv_id": conv.id,
                "name": name,
                "ducky_name": str(getattr(conv, "ducky_name", None) or prev.get("ducky_name") or ""),
                "ducky_style": str(getattr(conv, "ducky_style", None) or prev.get("ducky_style") or ""),
                "model": str(getattr(conv, "model", None) or prev.get("model") or ""),
                "coding_agent": str(getattr(conv, "coding_agent", None) or prev.get("coding_agent") or ""),
                "tts_voice": str(getattr(conv, "tts_voice", None) or prev.get("tts_voice") or ""),
                "tts_speed": float(getattr(conv, "tts_speed", None) or prev.get("tts_speed") or 0.0),
                "profile_id": str(prev.get("profile_id") or ""),
                "is_group": False,
            },
            index=len(new_rows),
        )
        new_rows.append(row)
        if not parent_id:
            conv.parent_conv_id = group_id
            save_conversation(conv, project_root)

    for folder in folders:
        hub = (getattr(folder, "group_hub_id", None) or "").strip()
        if not hub or hub == group_id:
            continue
        if (folder.parent_id or "") != folder_id:
            continue
        prev = existing.get(hub) or {}
        label = str(folder.name or prev.get("name") or "Group").strip() or "Group"
        new_rows.append(
            normalize_member(
                {
                    **prev,
                    "member_conv_id": hub,
                    "name": label,
                    "ducky_name": label,
                    "profile_id": str(prev.get("profile_id") or ""),
                    "is_group": True,
                },
                index=len(new_rows),
            )
        )

    # Designated leader must stay on the IN THIS CHAT roster (people + groups).
    # Sync used to rebuild only from folder contents and could orphan leader_conv_id.
    leader_id = (getattr(group, "leader_conv_id", None) or "").strip()
    if leader_id and leader_id != group_id:
        present = {str(r.get("member_conv_id") or "") for r in new_rows}
        if leader_id not in present:
            from frontend.ui_web.project_chats import move_conversation

            leader = by_id.get(leader_id) or load_conversation(leader_id, project_root=project_root)
            if leader is not None:
                prev = existing.get(leader_id) or {}
                if getattr(leader, "is_group", False):
                    label = (
                        str(prev.get("name") or "").strip()
                        or str(getattr(leader, "ducky_name", None) or "").strip()
                        or str(leader.title or "").strip()
                        or "Group"
                    )
                    new_rows.insert(
                        0,
                        normalize_member(
                            {
                                **prev,
                                "member_conv_id": leader_id,
                                "name": label,
                                "ducky_name": label,
                                "profile_id": str(prev.get("profile_id") or ""),
                                "is_group": True,
                            },
                            index=0,
                        ),
                    )
                else:
                    if (getattr(leader, "folder_id", None) or "") != folder_id:
                        move_conversation(leader_id, folder_id, project_root)
                        leader = load_conversation(leader_id, project_root=project_root) or leader
                    if (getattr(leader, "parent_conv_id", None) or "").strip() != group_id:
                        leader.parent_conv_id = group_id
                        save_conversation(leader, project_root)
                    name = (
                        str(prev.get("name") or "").strip()
                        or str(getattr(leader, "ducky_name", None) or "").strip()
                        or str(leader.title or "").strip()
                        or "Ducky"
                    )
                    new_rows.insert(
                        0,
                        normalize_member(
                            {
                                **prev,
                                "member_conv_id": leader_id,
                                "name": name,
                                "ducky_name": str(
                                    getattr(leader, "ducky_name", None) or prev.get("ducky_name") or ""
                                ),
                                "ducky_style": str(
                                    getattr(leader, "ducky_style", None) or prev.get("ducky_style") or ""
                                ),
                                "model": str(getattr(leader, "model", None) or prev.get("model") or ""),
                                "coding_agent": str(
                                    getattr(leader, "coding_agent", None) or prev.get("coding_agent") or ""
                                ),
                                "tts_voice": str(
                                    getattr(leader, "tts_voice", None) or prev.get("tts_voice") or ""
                                ),
                                "tts_speed": float(
                                    getattr(leader, "tts_speed", None) or prev.get("tts_speed") or 0.0
                                ),
                                "profile_id": str(prev.get("profile_id") or ""),
                                "is_group": False,
                            },
                            index=0,
                        ),
                    )
        else:
            # Keep leader first among people + nested groups.
            lead_row = next(r for r in new_rows if r.get("member_conv_id") == leader_id)
            new_rows = [lead_row, *[r for r in new_rows if r.get("member_conv_id") != leader_id]]

    group.group_members = new_rows
    save_conversation(group, project_root)
    return new_rows


def group_leader_member(
    group: Any, members: list[dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """Return the designated leader roster row, or the first leaf as fallback."""
    rows = members if members is not None else group_members(group)
    if not rows:
        return None
    leader_id = (getattr(group, "leader_conv_id", None) or "").strip()
    if leader_id:
        for m in rows:
            if str(m.get("member_conv_id") or "").strip() == leader_id:
                return m
    # Prefer a non-nested-group leaf for the fallback spokesperson.
    for m in rows:
        if not m.get("is_group"):
            return m
    return rows[0]


def resolve_nested_representative(
    speaker: dict[str, Any],
    question: str,
    *,
    profiles: list[dict[str, Any]] | None = None,
    visited: set[str] | None = None,
    depth: int = 0,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """If speaker is a nested group, route to its designated leader. Returns (run_as, publish_as)."""
    del profiles  # leader replaces when_to_use auto-pick for nested groups
    if not speaker.get("is_group"):
        return speaker, speaker
    gid = str(speaker.get("member_conv_id") or "").strip()
    seen = visited if visited is not None else set()
    if not gid or gid in seen or depth >= _NESTED_GROUP_DEPTH:
        return None, speaker
    seen.add(gid)
    sub = load_conversation(gid)
    if not sub or not is_group_conversation(sub):
        return None, speaker
    sync_group_members_from_folder(sub)
    leafs = group_members(sub)
    if not leafs:
        return None, speaker
    pick = group_leader_member(sub, leafs)
    if pick is None:
        return None, speaker
    run_as, publish_as = resolve_nested_representative(
        pick,
        question,
        visited=seen,
        depth=depth + 1,
    )
    if run_as is None:
        return None, speaker
    group_label = member_display_name(speaker)
    rep_label = member_display_name(publish_as if publish_as.get("is_group") else run_as)
    # Nested group → show "Subgroup — Leader" on the parent feed.
    badge = dict(publish_as if publish_as is not run_as else run_as)
    if " — " not in str(badge.get("name") or ""):
        badge["name"] = f"{group_label} — {rep_label}"
    else:
        badge["name"] = f"{group_label} — {badge.get('name')}"
    badge["color"] = str(speaker.get("color") or badge.get("color") or "")
    badge["is_group"] = False
    return run_as, badge


def broadcast_group_briefing(
    group_id: str,
    summary: str,
    *,
    from_name: str = "",
    project_root: str | None = None,
    skip_member_ids: set[str] | None = None,
) -> int:
    """Append a short [group-briefing] note to every leaf member chat in the group."""
    text = (summary or "").strip()
    if not text:
        return 0
    group = load_conversation(group_id, project_root=project_root)
    if not group or not is_group_conversation(group):
        return 0
    sync_group_members_from_folder(group, project_root=project_root)
    group = load_conversation(group_id, project_root=project_root) or group
    who = (from_name or "Teammate").strip() or "Teammate"
    body = (
        f"[group-briefing] {who}: {_truncate_brief(text)}\n"
        "(Shared with the whole group so everyone stays aware.)"
    )
    skip = skip_member_ids or set()
    count = 0
    for m in group_members(group):
        mid = str(m.get("member_conv_id") or "").strip()
        if not mid or m.get("is_group") or mid in skip:
            continue
        member = load_conversation(mid, project_root=project_root)
        if member is None:
            continue
        append_message(
            member,
            {"role": "user", "content": body, "ts": time.time(), "group_briefing": True},
            project_root=project_root,
        )
        count += 1
    return count


def _truncate_brief(text: str, limit: int = 1200) -> str:
    t = text.replace("\n", " ").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 1] + "…"


def is_group_turn_prompt(text: str) -> bool:
    """True for orchestrator-injected member turns (not a private user DM)."""
    t = (text or "").strip()
    return t.startswith('You are "') and "multi-ducky group chat" in t


def _message_plain_text(msg: dict[str, Any]) -> str:
    if msg.get("group_briefing"):
        return ""
    text = msg.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    return ""


def announce_private_member_talk(
    member_conv_id: str,
    *,
    push: PushFn | None = None,
    project_root: str | None = None,
) -> bool:
    """After a private DM with a group member, post a short note on the group hub.

    So everyone sees that the user talked to them and about what — without
    dumping the full side-chat transcript into the roundtable.
    """
    mid = (member_conv_id or "").strip()
    if not mid:
        return False
    member = load_conversation(mid, project_root=project_root)
    if not member or is_group_conversation(member):
        return False
    group_id = (getattr(member, "parent_conv_id", None) or "").strip()
    if not group_id:
        return False
    group = load_conversation(group_id, project_root=project_root)
    if not group or not is_group_conversation(group):
        return False

    user_text = ""
    reply = ""
    for msg in reversed(list(getattr(member, "messages", None) or [])):
        role = msg.get("role")
        if role == "assistant":
            if not reply:
                content = msg.get("content") or msg.get("text") or ""
                if isinstance(content, str) and content.strip():
                    reply = content.strip()
            continue
        if role == "user" and reply:
            plain = _message_plain_text(msg)
            if not plain:
                continue
            if is_group_turn_prompt(plain):
                return False
            user_text = plain
            break
    if not user_text or not reply:
        return False

    sync_group_members_from_folder(group, project_root=project_root)
    group = load_conversation(group_id, project_root=project_root) or group
    speaker: dict[str, Any] | None = None
    for row in group_members(group):
        if str(row.get("member_conv_id") or "") == mid:
            speaker = row
            break
    if speaker is None:
        speaker = {
            "member_conv_id": mid,
            "name": str(
                getattr(member, "ducky_name", None) or getattr(member, "title", None) or "Ducky"
            ),
            "tts_voice": str(getattr(member, "tts_voice", None) or ""),
            "tts_speed": float(getattr(member, "tts_speed", None) or 0.0),
        }

    note = (
        f"(Side chat) The user talked to me privately about: "
        f"{_truncate_brief(user_text, 240)}\n"
        f"What I said: {_truncate_brief(reply, 400)}"
    )
    author = author_payload(speaker)
    append_group_assistant(
        group,
        text=note,
        author=author,
        project_root=project_root,
        side_chat_announce=True,
    )
    if push is not None:
        push({"type": "text_delta", "text": note, "conv_id": group_id})
        push(
            {
                "type": "assistant_done",
                "conv_id": group_id,
                "author": author,
                "text": note,
            }
        )
    try:
        broadcast_group_briefing(
            group_id,
            note,
            from_name=member_display_name(speaker),
            project_root=project_root,
            skip_member_ids={mid},
        )
    except Exception:
        pass
    return True

def _member_aliases(member: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("name", "ducky_name"):
        val = str(member.get(key) or "").strip()
        if val and val not in out:
            out.append(val)
    return out


def find_member_by_name(members: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    key = (name or "").strip().lower()
    if not key:
        return None
    for m in members:
        for alias in _member_aliases(m):
            if alias.lower() == key:
                return m
    # Allow matching without spaces: "@VerseCoder" → "Verse Coder"
    compact = key.replace(" ", "")
    for m in members:
        for alias in _member_aliases(m):
            if alias.lower().replace(" ", "") == compact:
                return m
    return None


def extract_mention_target(
    reply: str,
    members: list[dict[str, Any]],
    *,
    self_name: str,
) -> tuple[dict[str, Any], str] | None:
    """Return (member, question) for the first @OtherName mention, else None.

    Matches known member names (longest first) so spaces in names like
    \"Level Designer\" do not greedily swallow the rest of the sentence.
    """
    text = reply or ""
    self_low = (self_name or "").strip().lower()
    # Longest alias first so \"@Verse Coder Pro\" beats \"@Verse\" if both exist.
    alias_rows: list[tuple[str, dict[str, Any]]] = []
    for m in members:
        for alias in _member_aliases(m):
            alias_rows.append((alias, m))
    alias_rows.sort(key=lambda row: len(row[0]), reverse=True)
    low = text.lower()
    best: tuple[int, dict[str, Any], int] | None = None  # start, member, end
    for name, m in alias_rows:
        if name.lower() == self_low:
            continue
        # Skip self when addressed by another alias (role vs ducky name).
        if any(a.lower() == self_low for a in _member_aliases(m)):
            continue
        needle = f"@{name.lower()}"
        at = low.find(needle)
        if at < 0:
            # Compact form: @LevelDesigner
            compact = f"@{name.lower().replace(' ', '')}"
            at = low.find(compact)
            if at < 0:
                continue
            end = at + len(compact)
        else:
            end = at + len(needle)
        if best is None or at < best[0] or (at == best[0] and end > best[2]):
            best = (at, m, end)
    if best is None:
        return None
    _at, target, end = best
    question = text[end:].strip()
    if not question:
        question = text.strip()
    return target, question


def _transcript_snippet(conv: Any, limit: int = _TRANSCRIPT_LINES) -> str:
    lines: list[str] = []
    for m in conv.messages[-limit:]:
        role = m.get("role")
        if role == "user":
            text = str(m.get("text") or m.get("content") or "").strip()
            if text:
                lines.append(f"User: {text}")
        elif role == "assistant":
            text = str(m.get("content") or m.get("text") or "").strip()
            if not text:
                continue
            author = m.get("author") if isinstance(m.get("author"), dict) else {}
            name = str(author.get("name") or "Ducky").strip() or "Ducky"
            lines.append(f"{name}: {text}")
    return "\n".join(lines)


def build_member_prompt(
    *,
    member_name: str,
    members: list[dict[str, Any]],
    transcript: str,
    message: str,
    from_name: str,
    roundtable: bool = False,
    is_leader: bool = False,
) -> str:
    roster = ", ".join(member_display_name(m) for m in members) or member_name
    my_role = ""
    for m in members:
        if member_display_name(m) == member_name or str(m.get("name") or "") == member_name:
            role = str(m.get("name") or "").strip()
            if role and role.lower() != member_name.lower():
                my_role = role
            break
    others = [
        member_display_name(m)
        for m in members
        if member_display_name(m) != member_name and str(m.get("name") or "") != member_name
    ]
    mention_hint = (
        f"To ask a teammate for help, write @{others[0]} followed by your question "
        f"(other teammates: {', '.join('@' + n for n in others)})."
        if others
        else "You are the only ducky in this group — answer the user directly."
    )
    bits = [
        f'You are "{member_name}"'
        + (f' (role: {my_role})' if my_role else "")
        + (" — GROUP LEADER" if is_leader else "")
        + f" in a multi-ducky group chat with: {roster}.",
        "You keep your own tools, skills, and memory in this conversation.",
        mention_hint,
        "Speak as yourself. Keep answers useful and concise for a live voice roundtable.",
        "Do not pretend to be another ducky.",
        "When the user asks for a plan, roadmap, or execution-ready checklist: "
        "1) create or update a real Plan with ducky_create_plan / ducky_update_plan "
        "on THIS conversation (not chat-only text); "
        "2) then reply in the group in 1–3 short lines: what you own, the exact plan title, "
        "and that it's ready to open — e.g. 'Plan ready: Roguelike room kit (open my Plan tab)'. "
        "Do not invent URLs; the UI links the plan from your create_plan call.",
    ]
    if is_leader:
        bits.append(
            "As group leader: you speak for this group to peer/parent group leaders. "
            "Break work into a plan on the group hub and create per-member plans "
            "(ducky_create_plan with chat_id=<member>) before @mentioning specialists. "
            "Summarize findings clearly so the whole group stays aware."
        )
    if roundtable:
        bits.append(
            "This is a roll-call / presence question. Answer in ONE short line as yourself "
            "(who you are / that you're here). Do not summarize the whole group."
        )
    if transcript.strip():
        bits.append("Recent group transcript:\n" + transcript.strip())
    bits.append(f"Current message from {from_name}:\n{message.strip()}")
    return "\n\n".join(bits)


def pick_member_for_question(
    question: str,
    members: list[dict[str, Any]],
    *,
    profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Cheap router: match when_to_use / personality, else first member."""
    if not members:
        raise ValueError("Group has no members")
    if len(members) == 1:
        return members[0]

    profile_by_id = {
        str(p.get("id") or ""): p for p in (profiles or []) if isinstance(p, dict)
    }
    roster_lines: list[str] = []
    for i, m in enumerate(members):
        pid = str(m.get("profile_id") or "")
        p = profile_by_id.get(pid) or {}
        when = str(p.get("when_to_use") or "").strip()
        personality = str(p.get("ducky_personality") or "").strip()[:200]
        roster_lines.append(
            f"{i + 1}. {m.get('name')} — when_to_use: {when or '(general)'}"
            + (f"; personality: {personality}" if personality else "")
        )

    system = (
        "You route a user question to the best specialist ducky. "
        "Reply with ONLY the member number (an integer). No other text."
    )
    user = (
        "Duckies:\n"
        + "\n".join(roster_lines)
        + "\n\nUser question:\n"
        + (question or "").strip()[:4000]
        + "\n\nBest member number:"
    )
    try:
        from frontend.ui_web.plugin_llm import _complete_text, _resolve_api_model
        import asyncio

        from backend.agent.batch_backends import supports_batch_complete

        provider_name, model_id = _resolve_api_model(model="")
        if not supports_batch_complete(provider_name):
            raise ValueError("router needs an API model")
        raw = asyncio.run(
            _complete_text(
                provider_name=provider_name,
                model=model_id,
                system=system,
                user=user,
            )
        )
        digits = re.search(r"\d+", (raw or "").strip())
        if digits:
            idx = int(digits.group(0)) - 1
            if 0 <= idx < len(members):
                return members[idx]
    except Exception:
        pass

    # Keyword fallback before first-member default.
    q = (question or "").lower()
    best = members[0]
    best_score = 0
    for m in members:
        pid = str(m.get("profile_id") or "")
        p = profile_by_id.get(pid) or {}
        blob = " ".join(
            [
                str(m.get("name") or ""),
                str(p.get("when_to_use") or ""),
                str(p.get("ducky_personality") or "")[:300],
            ]
        ).lower()
        score = sum(1 for tok in re.findall(r"[a-z]{4,}", blob) if tok in q)
        if score > best_score:
            best_score = score
            best = m
    return best


def member_display_name(member: dict[str, Any]) -> str:
    """Library ducky name (Verse Coder), not avatar style (Artist)."""
    # `name` is the profile/library title; prefer it over legacy ducky_name
    # values that were mistakenly set to style labels.
    for key in ("name", "ducky_name"):
        text = str(member.get(key) or "").strip()
        if text:
            return text
    style = str(member.get("ducky_style") or "").strip()
    if style:
        try:
            from frontend.ducky_assets import ducky_style_label

            return ducky_style_label(style)
        except Exception:
            pass
    return "Ducky"


def wants_all_speakers(question: str) -> bool:
    """True for roll-call / presence prompts — every member should answer."""
    return bool(_ROUNDTABLE_RE.search(question or ""))


def author_payload(member: dict[str, Any]) -> dict[str, str]:
    return {
        "name": member_display_name(member),
        "member_conv_id": str(member.get("member_conv_id") or ""),
        "tts_voice": str(member.get("tts_voice") or ""),
        "tts_speed": float(member.get("tts_speed") or 0.0),
        "color": str(member.get("color") or ""),
        "profile_id": str(member.get("profile_id") or ""),
    }


def append_group_assistant(
    group_conv: Any,
    *,
    text: str,
    author: dict[str, Any],
    project_root: str | None = None,
    side_chat_announce: bool = False,
) -> None:
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": text,
        "text": text,
        "ts": time.time(),
        "author": author_payload(author),
    }
    if side_chat_announce:
        msg["side_chat_announce"] = True
    append_message(group_conv, msg, project_root=project_root)


_group_sessions: dict[str, threading.Event] = {}
_group_lock = threading.Lock()


def is_group_running(group_id: str) -> bool:
    ev = _group_sessions.get(group_id)
    return bool(ev and not ev.is_set())


def cancel_group_run(group_id: str) -> None:
    ev = _group_sessions.get(group_id)
    if ev:
        ev.set()


def run_group_turn(
    group_id: str,
    user_text: str,
    *,
    mode: str = "agent",
    model: str = "",
    push: PushFn | None = None,
    timeout_sec: float = 180.0,
) -> str:
    """Start a group orchestrator thread. Returns run_id (empty on immediate failure)."""
    from frontend.ui_web.agent_modes import get_panel_push, is_agent_running

    push_fn: PushFn = push or get_panel_push() or (lambda _e: None)
    conv = load_conversation(group_id)
    if not conv or not is_group_conversation(conv):
        push_fn({"type": "error", "text": "Not a group chat", "conv_id": group_id})
        return ""
    # Folder membership wins — drag a ducky/group into the group folder to nest it.
    sync_group_members_from_folder(conv)
    conv = load_conversation(group_id) or conv
    members = group_members(conv)
    if not members:
        push_fn(
            {
                "type": "error",
                "text": "Invite at least one ducky before chatting in this group.",
                "conv_id": group_id,
            }
        )
        return ""
    if is_group_running(group_id) or is_agent_running(group_id):
        push_fn({"type": "error", "text": "Group is already running a turn", "conv_id": group_id})
        return ""

    text = (user_text or "").strip()
    if not text:
        push_fn({"type": "error", "text": "Empty message", "conv_id": group_id})
        return ""

    run_id = str(uuid.uuid4())
    cancel = threading.Event()
    with _group_lock:
        _group_sessions[group_id] = cancel

    # Persist the user turn on the group transcript immediately.
    append_message(
        conv,
        {"role": "user", "content": text, "text": text, "ts": time.time()},
    )
    if sum(1 for m in conv.messages if m.get("role") == "user") == 1:
        auto_title(conv, text)

    def worker() -> None:
        try:
            _run_group_turn_body(
                group_id=group_id,
                user_text=text,
                mode=mode,
                model=model,
                push=push_fn,
                run_id=run_id,
                cancel=cancel,
                timeout_sec=timeout_sec,
            )
        finally:
            with _group_lock:
                if _group_sessions.get(group_id) is cancel:
                    _group_sessions.pop(group_id, None)
            push_fn(
                {
                    "type": "agent_stopped",
                    "conv_id": group_id,
                    "run_id": run_id,
                    "reason": "cancelled" if cancel.is_set() else "done",
                }
            )

    threading.Thread(target=worker, name=f"group-orch-{group_id[:8]}", daemon=True).start()
    return run_id


def _publish_member_reply(
    *,
    group_id: str,
    speaker: dict[str, Any],
    reply: str,
    push: PushFn,
    run_id: str,
    lock: threading.Lock,
) -> None:
    """Persist + push one member reply to the group feed (thread-safe)."""
    author = author_payload(speaker)
    with lock:
        group = load_conversation(group_id)
        if not group:
            return
        append_group_assistant(group, text=reply, author=author)
        # Full reply on text_delta + text on assistant_done so the UI commits
        # even if a delta was missed while status was still "sending".
        push(
            {
                "type": "text_delta",
                "text": reply,
                "conv_id": group_id,
                "run_id": run_id,
            }
        )
        push(
            {
                "type": "assistant_done",
                "conv_id": group_id,
                "run_id": run_id,
                "author": author,
                "text": reply,
            }
        )
    # Share findings with every member chat so private contexts stay aware.
    try:
        broadcast_group_briefing(
            group_id,
            reply,
            from_name=member_display_name(speaker),
        )
    except Exception:
        pass


def _member_push_to_group(
    *,
    group_id: str,
    run_id: str,
    author: dict[str, str],
    push: PushFn,
    lock: threading.Lock,
) -> PushFn:
    """Relay member tool/status/plan events onto the group feed so thinking is visible."""
    relay_kinds = frozenset(
        {
            "tool",
            "tool_done",
            "status",
            "plan_updated",
            "thinking",
            "thinking_delta",
        }
    )

    def relay(event: dict[str, Any]) -> None:
        kind = str(event.get("type") or "")
        if kind not in relay_kinds:
            return
        forwarded = dict(event)
        forwarded["conv_id"] = group_id
        forwarded["run_id"] = run_id
        forwarded["author"] = author
        # plan_updated stays keyed to the member chat_id inside plan — UI can open it.
        with lock:
            push(forwarded)

    return relay


def _run_member_turn(
    *,
    group_id: str,
    speaker: dict[str, Any],
    members: list[dict[str, Any]],
    pending_text: str,
    pending_from: str,
    mode: str,
    model: str,
    timeout_sec: float,
    roundtable: bool,
    cancel: threading.Event,
    push: PushFn | None = None,
    run_id: str = "",
    publish_lock: threading.Lock | None = None,
    publish_as: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str, str]:
    """Run one member in the background. Returns (publish_speaker, reply, error)."""
    from frontend.ui_web.agent_modes import run_message_and_wait

    if cancel.is_set():
        return publish_as or speaker, "", "cancelled"
    badge = publish_as or speaker
    display = member_display_name(badge)
    # Prompt roster/identity uses the leaf speaker name when a nested group
    # picked a rep — the badge name is for the group feed only.
    prompt_name = member_display_name(speaker)
    group = load_conversation(group_id)
    transcript = _transcript_snippet(group) if group else ""
    leader_id = (getattr(group, "leader_conv_id", None) or "").strip() if group else ""
    speaker_id = str(speaker.get("member_conv_id") or "").strip()
    prompt = build_member_prompt(
        member_name=prompt_name,
        members=members,
        transcript=transcript,
        message=pending_text,
        from_name=pending_from,
        roundtable=roundtable,
        is_leader=bool(leader_id and speaker_id == leader_id),
    )
    member_conv = load_conversation(str(speaker["member_conv_id"]))
    member_model = (getattr(member_conv, "model", None) or "").strip() if member_conv else ""
    # parent="" — do NOT nest under the group. Nesting fires linked_agent +
    # chats_changed which opens/focuses the member tab and blocks the group UI
    # on "waiting for linked agent" while the reply already finished.
    member_id = str(speaker["member_conv_id"])
    author = author_payload(badge)
    member_push: PushFn | None = None
    if push is not None and run_id:
        member_push = _member_push_to_group(
            group_id=group_id,
            run_id=run_id,
            author=author,
            push=push,
            lock=publish_lock or threading.Lock(),
        )
    result = run_message_and_wait(
        member_id,
        prompt,
        mode=mode or "agent",
        model=member_model or (model or ""),
        timeout_sec=timeout_sec,
        push=member_push,
        cancel_on_timeout=True,
        parent="",
    )
    if cancel.is_set():
        return badge, "", "cancelled"
    reply = str(result.get("assistant_text") or "").strip()
    if result.get("status") != "done" or not reply:
        err = str(result.get("error") or "Member did not reply")
        return badge, "", err
    # Surface member plans on the group feed (no chat-to-chat nesting).
    try:
        from backend.agent.coding_agents.plans import load_plan, push_plan_updated, todo_progress

        member_plan = load_plan(member_id)
        if member_plan:
            push_plan_updated(member_plan)
            if push is not None and run_id:
                title = str(member_plan.get("title") or "Plan").strip() or "Plan"
                prog = todo_progress(member_plan)
                with (publish_lock or threading.Lock()):
                    push(
                        {
                            "type": "status",
                            "text": f'{display} created plan “{title}” ({prog.get("total", 0)} steps) — open Plan tab',
                            "conv_id": group_id,
                            "run_id": run_id,
                            "author": author,
                            "plan": member_plan,
                            "progress": prog,
                        }
                    )
                    # Also refresh the group's visible plan popup to this member's plan.
                    push(
                        {
                            "type": "plan_updated",
                            "conv_id": group_id,
                            "run_id": run_id,
                            "author": author,
                            "plan": member_plan,
                            "progress": prog,
                        }
                    )
    except Exception:
        pass
    return badge, reply, ""


def _run_group_turn_body(
    *,
    group_id: str,
    user_text: str,
    mode: str,
    model: str,
    push: PushFn,
    run_id: str,
    cancel: threading.Event,
    timeout_sec: float,
) -> None:
    from frontend.agent_profiles import list_agent_profiles_available

    group = load_conversation(group_id)
    if not group:
        return
    sync_group_members_from_folder(group)
    group = load_conversation(group_id) or group
    members = group_members(group)
    profiles = list_agent_profiles_available()
    publish_lock = threading.Lock()
    roundtable = wants_all_speakers(user_text)
    visited_base = {group_id}

    def _prepare(member: dict[str, Any], question: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        return resolve_nested_representative(
            member,
            question,
            profiles=profiles,
            visited=set(visited_base),
        )

    if roundtable:
        # Everyone answers — nested groups speak via one auto-picked rep.
        names = ", ".join(member_display_name(m) for m in members)
        push(
            {
                "type": "status",
                "text": f"Roundtable — {names}…",
                "conv_id": group_id,
                "run_id": run_id,
            }
        )
        prepared: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for m in members:
            run_as, pub_as = _prepare(m, user_text)
            if run_as is None:
                push(
                    {
                        "type": "error",
                        "text": f"{member_display_name(m)}: no representative available",
                        "conv_id": group_id,
                        "run_id": run_id,
                    }
                )
                continue
            prepared.append((run_as, pub_as))
        workers = min(8, max(1, len(prepared)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [
                pool.submit(
                    _run_member_turn,
                    group_id=group_id,
                    speaker=run_as,
                    members=members,
                    pending_text=user_text,
                    pending_from="User",
                    mode=mode,
                    model=model,
                    timeout_sec=timeout_sec,
                    roundtable=True,
                    cancel=cancel,
                    push=push,
                    run_id=run_id,
                    publish_lock=publish_lock,
                    publish_as=pub_as,
                )
                for run_as, pub_as in prepared
            ]
            for fut in as_completed(futs):
                if cancel.is_set():
                    return
                speaker, reply, err = fut.result()
                label = member_display_name(speaker)
                if err:
                    push(
                        {
                            "type": "error",
                            "text": f"{label}: {err}",
                            "conv_id": group_id,
                            "run_id": run_id,
                        }
                    )
                    continue
                push(
                    {
                        "type": "status",
                        "text": f"{label} replied",
                        "conv_id": group_id,
                        "run_id": run_id,
                    }
                )
                _publish_member_reply(
                    group_id=group_id,
                    speaker=speaker,
                    reply=reply,
                    push=push,
                    run_id=run_id,
                    lock=publish_lock,
                )
        return

    # Task turn: one specialist (or nested-group rep), then optional @mention hops.
    routed = pick_member_for_question(user_text, members, profiles=profiles)
    pending_from = "User"
    pending_text = user_text
    hops = 0
    # Track who spoke last for mention self-skip (roster row, not badge).
    last_roster = routed

    while routed and hops <= _MENTION_HOP_BUDGET:
        if cancel.is_set():
            return
        members = group_members(load_conversation(group_id) or group)
        run_as, pub_as = _prepare(routed, pending_text)
        if run_as is None:
            push(
                {
                    "type": "error",
                    "text": f"{member_display_name(routed)}: no representative available",
                    "conv_id": group_id,
                    "run_id": run_id,
                }
            )
            return
        display = member_display_name(pub_as)
        push(
            {
                "type": "status",
                "text": f"{display} is thinking…",
                "conv_id": group_id,
                "run_id": run_id,
            }
        )
        published, reply, err = _run_member_turn(
            group_id=group_id,
            speaker=run_as,
            members=members,
            pending_text=pending_text,
            pending_from=pending_from,
            mode=mode,
            model=model,
            timeout_sec=timeout_sec,
            roundtable=False,
            cancel=cancel,
            push=push,
            run_id=run_id,
            publish_lock=publish_lock,
            publish_as=pub_as,
        )
        if cancel.is_set():
            return
        if err:
            push(
                {
                    "type": "error",
                    "text": f"{display}: {err}",
                    "conv_id": group_id,
                    "run_id": run_id,
                }
            )
            return
        _publish_member_reply(
            group_id=group_id,
            speaker=published,
            reply=reply,
            push=push,
            run_id=run_id,
            lock=publish_lock,
        )

        mention = extract_mention_target(
            reply,
            members,
            self_name=str(last_roster.get("name") or ""),
        )
        if mention is None:
            mention = extract_mention_target(
                reply,
                members,
                self_name=member_display_name(last_roster),
            )
        if mention is None or hops >= _MENTION_HOP_BUDGET:
            return
        next_member, question = mention
        pending_from = member_display_name(published)
        pending_text = question
        routed = next_member
        last_roster = next_member
        hops += 1


def list_group_running_ids() -> list[str]:
    with _group_lock:
        return [gid for gid, ev in _group_sessions.items() if not ev.is_set()]
