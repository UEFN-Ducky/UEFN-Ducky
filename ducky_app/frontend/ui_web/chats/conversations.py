"""Conversations: load, list, create, settings, skills, tools, titles, move and delete."""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from frontend.archive_folder import is_archive_folder_id
from frontend.atomic_json import write_json_atomic
from frontend.chat_store import (
    Conversation,
    norm_file_path,
    normalize_ducky_style,
    remap_conversation_file_path,
)
from frontend.settings import PanelSettings
from frontend.ui_web.chats import folders as _folders
from frontend.ui_web.chats import groups as _groups
from frontend.ui_web.chats import saves as _saves
from frontend.ui_web.chats import store as _store

_sort_migrated_slugs: set[str] = set()


def _conversation_sort_key(conv: Conversation) -> tuple[float, float, str]:
    return (conv.sort_order, -conv.updated, conv.id)


def _sort_conversations(convs: list[Conversation]) -> list[Conversation]:
    return sorted(convs, key=_conversation_sort_key)


def _migrate_conversation_sort_orders(convs: list[Conversation]) -> list[Conversation]:
    """Assign sort_order from updated desc within each folder when all are zero."""
    changed: list[Conversation] = []
    by_folder: dict[str, list[Conversation]] = {}
    for conv in convs:
        by_folder.setdefault(conv.folder_id or "", []).append(conv)

    for group in by_folder.values():
        if not group:
            continue
        if all(c.sort_order == 0.0 for c in group):
            ordered = sorted(group, key=lambda c: c.updated, reverse=True)
            for idx, conv in enumerate(ordered):
                conv.sort_order = float(idx)
                changed.append(conv)
    return changed


def _load_all_conversations(
    project_root: str | None = None,
    *,
    include_messages: bool = True,
) -> list[Conversation]:
    from frontend.ui_web.session_files import session_stats_from_messages

    if _store._use_db():
        _saves.flush_chat_saves()  # a list reads the store: queued saves first (cheap when none)
        # Counts are maintained columns; a metadata list never touches message bodies.
        docs = _store._repo().conv_list(_store._project_id(project_root), with_messages=include_messages)
        return [Conversation.from_dict(doc) for doc in docs]
    out: list[Conversation] = []
    seen: set[str] = set()
    conv_dir = _store._conversations_dir(project_root)
    for path in conv_dir.iterdir():
        if not path.is_dir():
            continue
        meta = path / "conversation.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            msgs = data.get("messages") if isinstance(data.get("messages"), list) else []
            stats = session_stats_from_messages(msgs)
            data = {
                **data,
                "tool_call_count": stats["tool_call_count"],
                "file_count": stats["file_count"],
            }
            if not include_messages:
                data = {**data, "messages": []}
            conv = Conversation.from_dict(data)
            if conv.id in seen:
                continue
            seen.add(conv.id)
            out.append(conv)
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _maybe_migrate_conversation_sort_orders(project_root: str | None = None) -> None:
    """One-shot per process/project: fix zeroed sort_order without running on every list."""
    slug = _store._project_id(project_root)
    if slug in _sort_migrated_slugs:
        return
    with _store._conversation_migrate_lock(project_root):
        if slug in _sort_migrated_slugs:
            return
        all_convs = _load_all_conversations(project_root, include_messages=not _store._use_db())
        for conv in _migrate_conversation_sort_orders(all_convs):
            _saves.save_conversation(conv, project_root, touch_updated=False)
        _sort_migrated_slugs.add(slug)


def _load_conversations_for_slug(slug: str) -> list[Conversation]:
    if _store._use_db():
        return [Conversation.from_dict(d) for d in _store._repo().conv_list(slug, with_messages=False)]
    out: list[Conversation] = []
    conv_dir = _store._chats_root() / slug / "conversations"
    if not conv_dir.is_dir():
        return out
    for path in conv_dir.iterdir():
        if not path.is_dir():
            continue
        meta = path / "conversation.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            data = {**data, "messages": []}
            out.append(Conversation.from_dict(data))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def iter_conversations_by_project() -> list[tuple[str, Conversation]]:
    if _store._use_db():
        return [
            (pid, Conversation.from_dict(data))
            for pid, data in _store._repo().conv_list_all_projects(with_messages=False)
        ]
    out: list[tuple[str, Conversation]] = []
    for slug in _store._iter_disk_project_slugs():
        for conv in _load_conversations_for_slug(slug):
            out.append((slug, conv))
    return out


def list_conversations(folder_id: str | None = None, project_root: str | None = None) -> list[Conversation]:
    _maybe_migrate_conversation_sort_orders(project_root)
    all_convs = _load_all_conversations(project_root, include_messages=False)
    out = [c for c in all_convs if folder_id is None or c.folder_id == folder_id]
    return _sort_conversations(out)


def list_all_conversation_metadata(project_root: str | None = None) -> list[Conversation]:
    """All chats for the sidebar — metadata only (messages stripped)."""
    return list_conversations(folder_id=None, project_root=project_root)


def list_conversations_for_file(
    file_path: str,
    project_root: str | None = None,
) -> list[Conversation]:
    target = norm_file_path(file_path)
    if not target:
        return []
    return _sort_conversations(
        [
            c
            for c in _load_all_conversations(project_root, include_messages=False)
            if norm_file_path(c.file_path) == target
        ]
    )


def remap_conversation_file_paths(
    from_path: str,
    to_path: str,
    project_root: str | None = None,
) -> int:
    """Update stored ducky file paths after a project file move/rename."""
    changed = 0
    for conv in _load_all_conversations(project_root, include_messages=not _store._use_db()):
        next_path = remap_conversation_file_path(conv.file_path, from_path, to_path)
        if next_path != norm_file_path(conv.file_path):
            conv.file_path = next_path
            _saves.save_conversation(conv, project_root, touch_updated=False)
            changed += 1
    return changed


def conversation_path(conv_id: str, project_root: str | None = None) -> Path:
    """Primary save path: folder layout conversation.json."""
    from frontend.ui_web.conversation_attachments import conversation_meta_path

    return conversation_meta_path(conv_id, _store._conversations_dir(project_root))


def _find_conversation_meta(conv_id: str, project_root: str | None = None) -> Path | None:
    primary = conversation_path(conv_id, project_root)
    if primary.is_file():
        return primary
    for meta in _iter_all_conversation_meta_paths():
        if meta.parent.name == conv_id:
            return meta
    return None


def conversation_project_slug(conv_id: str, project_root: str | None = None) -> str | None:
    """Return the stored owner, regardless of the project used to find the chat.

    An unknown chat returns None; a projectless chat belongs to ``_no_project``.
    """
    if _store._use_db():
        return _store._repo().conv_project_id(conv_id)
    meta = _find_conversation_meta(conv_id, project_root)
    return meta.parent.parent.parent.name if meta is not None else None


def load_conversation(conv_id: str, project_root: str | None = None) -> Conversation | None:
    """Load a globally unique chat ID; project_root is only a lookup hint."""
    if _store._use_db():
        writer = _saves._WRITER
        if writer is not None and writer.checkpoint_waits(conv_id):
            writer.flush(conv_id)  # its in-flight row is only in the queue: write it first
        queued = _saves._queued(conv_id)
        if queued is not None:
            return _held(_saves._from_queue(queued, with_messages=True))
        # Ids are globally unique — don't filter by the active island slug.
        doc = _store._repo().conv_get(conv_id, with_messages=False)
        if doc is None:
            return None
        # Rows, rev and row hashes from one snapshot, so the next save goes by delta.
        msgs, rev, hashes = _store._repo().messages_get_rev(conv_id, with_hashes=True)
        conv = Conversation.from_dict(doc)
        conv.swap_messages(msgs, None)
        conv._dirty_from, conv._msg_rev, conv._base_hashes = None, rev, hashes
        conv._dirty_rows = set()
        return _held(conv)
    meta = _find_conversation_meta(conv_id, project_root)
    if meta is None:
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return _held(Conversation.from_dict(data))
    except (json.JSONDecodeError, OSError):
        pass
    return None


def load_conversation_meta(conv_id: str, project_root: str | None = None) -> Conversation | None:
    """The chat for its names and settings; the row store skips its messages. A
    coordinator's chat can hold hundreds; parsing them all to read a title took tens of ms."""
    if _store._use_db():
        queued = _saves._queued(conv_id)
        if queued is not None:
            return _held(_saves._from_queue(queued, with_messages=False))
        doc = _store._repo().conv_get(conv_id, with_messages=False)
        return _held(Conversation.from_dict(doc)) if doc is not None else None
    return load_conversation(conv_id, project_root)


def conversation_title(conv_id: str, project_root: str | None = None) -> str:
    """The sidebar title only; never loads message bodies on the row store."""
    if _store._use_db():
        queued = _saves._queued(conv_id)
        if queued is not None:
            return str(queued.title or "").strip()
        doc = _store._repo().conv_get(conv_id, with_messages=False)
        return str((doc or {}).get("title") or "").strip()
    conv = load_conversation(conv_id, project_root)
    return str(getattr(conv, "title", "") or "").strip() if conv is not None else ""


def sync_skill_snapshot(
    conv: Conversation, settings: Any, project_root: str | None = None
) -> bool:
    """Rebuild a frozen skill index when the packs on disk changed.

    Returns True when the prompt text actually changed. A conversation freezes
    its skill index at creation so pack toggles elsewhere cannot rewrite an
    in-flight chat; without this, a chat opened before an update never learns
    that a new subskill exists (bodies are always read live, the index is not).

    Rebuilds from the conversation's OWN selection, so per-chat enables and
    disables survive. The prompt cache is only invalidated when the text really
    changed — a body-only fix moves the revision but not the index, and costs
    no cache miss.
    """
    from backend.skills.revision import skills_revision
    from backend.skills.selection import resolve_conversation_selection
    from backend.skills.skill_index import build_skill_prompt

    if not str(getattr(conv, "skill_snapshot", "") or "").strip():
        return False  # no snapshot: the send path rebuilds from selection anyway
    rev = skills_revision()
    if not rev or rev == str(getattr(conv, "skill_snapshot_rev", "") or ""):
        return False
    text = build_skill_prompt(resolve_conversation_selection(conv, settings))
    if not text.strip():
        return False  # never blank out a working snapshot
    changed = text.strip() != conv.skill_snapshot.strip()
    conv.skill_snapshot = text
    conv.skill_snapshot_rev = rev
    if changed:
        from backend.agent.prompt_cache import invalidate_conv_cache

        invalidate_conv_cache(conv)
    _saves.save_conversation(conv, project_root, touch_updated=False)
    return changed


# What apply_ducky_config and the web tools set. A turn holds a copy loaded before the edit.
_DUCKY_SETTINGS = (
    "ducky_style",
    "ducky_name",
    "profile_id",
    "ducky_personality",
    "tts_voice",
    "tts_speed",
    "thinking_effort",
    "web_access_allowed",
    "web_access_denied",
    "web_fetch_urls",
)


def _keep_newer_settings(conv: Conversation, stored: dict[str, Any] | None) -> None:
    """Saving an older copy never puts back the voice, name or style the user just changed."""
    if not stored:
        return
    edited = float(stored.get("settings_edited") or 0.0)
    if edited <= float(conv.settings_edited or 0.0):
        return
    fresh = Conversation.from_dict({**stored, "id": conv.id, "messages": []})
    for name in _DUCKY_SETTINGS:
        setattr(conv, name, getattr(fresh, name))
    conv.settings_edited = edited


# Where a chat sits and what it's called. A running turn holds a copy loaded before a
# move into a group, a drag between folders or a rename; saving it must not undo them.
# Mute rides along: muting a running member must survive its turn's save.
_PLACEMENT = ("title", "folder_id", "sort_order", "parent_conv_id", "leader_conv_id", "muted")
_HELD_ATTR = "_placement_held"


def _held(conv: Conversation) -> Conversation:
    """Remember this copy's placement as loaded (see :func:`_keep_newer_placement`)."""
    held = {name: getattr(conv, name) for name in _PLACEMENT}
    held["settings_edited"] = conv.settings_edited
    setattr(conv, _HELD_ATTR, held)
    return conv


def _placement_changed(conv: Conversation) -> bool:
    """A new chat, a move, rename, mute or settings edit: other processes read these at
    once (D23), so such a save waits until it is written."""
    held = getattr(conv, _HELD_ATTR, None)
    if not held:
        return True
    if conv.settings_edited != held.get("settings_edited"):
        return True
    return any(getattr(conv, name) != held.get(name) for name in _PLACEMENT)


def _keep_newer_placement(conv: Conversation, stored: dict[str, Any] | None) -> None:
    """A field this copy didn't change but someone else did since it was loaded keeps
    their value. Copies never loaded through load_conversation save as they are."""
    held = getattr(conv, _HELD_ATTR, None)
    if not held or not stored:
        return
    fresh = Conversation.from_dict({**stored, "id": conv.id, "messages": []})
    for name in _PLACEMENT:
        mine, theirs = getattr(conv, name), getattr(fresh, name)
        if mine == held.get(name) and theirs != mine:
            setattr(conv, name, theirs)


def all_available_tool_ids() -> list[str]:
    """Globally available tool-group ids (builtins + MCP plugins + UEFN agent plugins)."""
    from backend.agent.builtin_toolsets import get_enabled_builtin_group_ids
    from backend.mcp_plugins.store import get_enabled_plugin_ids
    from backend.uefn_plugins.host import ensure_plugins_loaded, uefn_agent_tool_rows

    ensure_plugins_loaded()
    ids = list(get_enabled_builtin_group_ids()) + list(get_enabled_plugin_ids())
    for row in uefn_agent_tool_rows():
        pid = str(row.get("id") or "").strip()
        if pid and pid not in ids:
            ids.append(pid)
    return ids


def create_conversation(
    settings: PanelSettings | None = None,
    folder_id: str = "",
    *,
    title: str = "",
    skill_snapshot: str = "",
    enabled_packs: list[str] | None = None,
    enabled_subskills: dict[str, list[str]] | None = None,
    enabled_skills: list[str] | None = None,
    disabled_packs: list[str] | None = None,
    ducky_style: str = "classic",
    ducky_name: str = "",
    profile_id: str = "",
    ducky_personality: str = "",
    tts_voice: str = "",
    tts_speed: float = 0.0,
    thinking_effort: str = "",
    file_path: str = "",
    tool_ids: list[str] | None = None,
    disabled_tool_ids: list[str] | None = None,
    model: str | None = None,
    provider: str | None = None,
    project_root: str | None = None,
    coding_agent: str | None = None,
    parent_conv_id: str = "",
    conv_id: str = "",
) -> Conversation:
    del enabled_packs, enabled_skills, tool_ids  # legacy pack/tool allowlists ignored
    now = time.time()
    s = settings or PanelSettings.load()
    root = project_root if project_root is not None else s.uefn_project_root
    from backend.skills.revision import skills_revision
    from backend.skills.selection import merge_selection
    from backend.skills.skill_index import conversation_skill_text
    from backend.agent.coding_agents.base import normalize_coding_agent
    from backend.agent.thinking_effort import normalize_thinking_effort

    sel = merge_selection(disabled_packs=disabled_packs, enabled_subskills=enabled_subskills)
    snapshot = skill_snapshot or conversation_skill_text(
        disabled_packs=sel.disabled_packs,
        enabled_subskills=sel.enabled_subskills,
        skill_snapshot="",
        mutate=False,
    )
    snapshot_rev = skills_revision()
    agent = normalize_coding_agent(coding_agent or "ducky")
    model_value = (model or "").strip()
    provider_value = (provider or "").strip()
    conv = Conversation(
        id=(conv_id or "").strip() or str(uuid.uuid4()),
        folder_id=folder_id or "",
        title=(title.strip()[:120] if title.strip() else "New ducky"),
        created=now,
        updated=now,
        sort_order=_next_sort_order(folder_id or "", root),
        provider=provider_value,
        model=model_value,
        skill_snapshot=snapshot,
        skill_snapshot_rev=snapshot_rev,
        disabled_packs=list(sel.disabled_packs),
        enabled_packs=None,
        enabled_subskills=dict(sel.enabled_subskills) or None,
        enabled_skills=None,
        ducky_style=normalize_ducky_style(ducky_style),
        ducky_name=(ducky_name or "").strip(),
        profile_id=(profile_id or "").strip(),
        ducky_personality=(ducky_personality or "").strip(),
        tts_voice=(tts_voice or "").strip(),
        tts_speed=float(tts_speed or 0.0),
        thinking_effort=normalize_thinking_effort(thinking_effort),
        file_path=norm_file_path(file_path),
        coding_agent=agent,
        parent_conv_id=(parent_conv_id or "").strip(),
    )
    _apply_disabled_tool_ids_to_conv(conv, disabled_tool_ids or [])
    _saves.save_conversation(conv, root)
    return conv


def _next_sort_order(folder_id: str, project_root: str | None) -> float:
    if _store._use_db():
        return _store._repo().conv_max_sort_order(_store._project_id(project_root), folder_id) + 1.0
    return max((c.sort_order for c in list_conversations(folder_id, project_root)), default=-1.0) + 1.0


def _apply_tool_ids_to_conv(conv: Conversation, tool_ids: list[str]) -> None:
    """Legacy allowlist writer — prefer _apply_disabled_tool_ids_to_conv."""
    from backend.agent.builtin_toolsets import is_builtin_group
    from backend.mcp_plugins.store import load_plugin_manifest as load_mcp_manifest
    from backend.mcp_plugins.store import normalize_plugin_id
    from backend.uefn_plugins.host import is_uefn_agent_tool_plugin

    builtin_sel: list[str] = []
    plugin_sel: list[str] = []
    uefn_sel: list[str] = []
    for item in tool_ids:
        pid = normalize_plugin_id(str(item))
        if is_builtin_group(pid):
            if pid not in builtin_sel:
                builtin_sel.append(pid)
            continue
        if is_uefn_agent_tool_plugin(pid):
            if pid not in uefn_sel:
                uefn_sel.append(pid)
            continue
        if load_mcp_manifest(pid) is not None and pid not in plugin_sel:
            plugin_sel.append(pid)
    conv.builtin_toolsets = builtin_sel
    conv.mcp_plugins = plugin_sel
    conv.uefn_plugins = uefn_sel


def _apply_disabled_tool_ids_to_conv(conv: Conversation, disabled_tool_ids: list[str]) -> None:
    """Write effective tool groups = all globally available minus the deny-list."""
    from backend.uefn_plugins.host import uefn_agent_tool_rows

    denied = {str(x).strip() for x in disabled_tool_ids if str(x).strip()}
    effective = [tid for tid in all_available_tool_ids() if tid not in denied]
    _apply_tool_ids_to_conv(conv, effective)
    # No UEFN app-plugin denied → follow Store enable so newly enabled plugins
    # appear in current sessions without rewriting every chat.
    available_uefn = {str(r.get("id") or "") for r in uefn_agent_tool_rows() if r.get("id")}
    if not (available_uefn & denied):
        conv.uefn_plugins = None


def _iter_all_conversation_meta_paths() -> list[Path]:
    root = _store._chats_root()
    if not root.is_dir():
        return []
    return list(root.glob("*/conversations/*/conversation.json"))


def opt_in_uefn_plugin_all_chats(plugin_id: str) -> int:
    """Add ``plugin_id`` to every chat with an explicit uefn_plugins allowlist."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    n = 0
    if _store._use_db():
        repo = _store._repo()
        _saves.flush_chat_saves()
        _saves._forget_saved()
        for project_id, data in repo.conv_list_all_projects(with_messages=False):
            uefn = data.get("uefn_plugins")
            if uefn is None or not isinstance(uefn, list):
                continue
            ids = [str(x) for x in uefn]
            if pid in ids:
                continue
            data["uefn_plugins"] = ids + [pid]
            data["prompt_cache_snapshot"] = None
            repo.conv_save(project_id, data, messages=None)
            n += 1
        return n
    for meta in _iter_all_conversation_meta_paths():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        uefn = data.get("uefn_plugins")
        if uefn is None or not isinstance(uefn, list):
            continue
        ids = [str(x) for x in uefn]
        if pid in ids:
            continue
        data["uefn_plugins"] = ids + [pid]
        data["prompt_cache_snapshot"] = None
        write_json_atomic(meta, data)
        n += 1
    return n


def invalidate_all_projects_conversation_caches() -> None:
    """Clear frozen prompt snapshots for every chat across all projects."""
    if _store._use_db():
        repo = _store._repo()
        _saves.flush_chat_saves()
        _saves._forget_saved()
        for project_id, data in repo.conv_list_all_projects(with_messages=False):
            if data.get("prompt_cache_snapshot") is None:
                continue
            data["prompt_cache_snapshot"] = None
            repo.conv_save(project_id, data, messages=None)
        return
    for meta in _iter_all_conversation_meta_paths():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict) or data.get("prompt_cache_snapshot") is None:
            continue
        data["prompt_cache_snapshot"] = None
        write_json_atomic(meta, data)


def apply_ducky_config(
    conv_id: str,
    *,
    ducky_style: str,
    ducky_name: str | None = None,
    profile_id: str | None = None,
    ducky_personality: str = "",
    tts_voice: str | None = None,
    tts_speed: float | None = None,
    thinking_effort: str | None = None,
    title: str | None = None,
    enabled_packs: list[str] | None = None,
    enabled_subskills: dict[str, list[str]] | None = None,
    tool_ids: list[str] | None = None,
    disabled_packs: list[str] | None = None,
    disabled_tool_ids: list[str] | None = None,
    project_root: str | None = None,
) -> None:
    del enabled_packs, tool_ids  # legacy allowlists ignored
    conv = load_conversation(conv_id, project_root)
    if not conv:
        raise ValueError(f"Conversation not found: {conv_id}")
    conv.ducky_style = normalize_ducky_style(ducky_style)
    if ducky_name is not None:
        conv.ducky_name = (ducky_name or "").strip()
    if profile_id is not None:
        conv.profile_id = (profile_id or "").strip()
    conv.ducky_personality = (ducky_personality or "").strip()
    if tts_voice is not None:
        conv.tts_voice = (tts_voice or "").strip()
    if tts_speed is not None:
        conv.tts_speed = float(tts_speed or 0.0)
    if thinking_effort is not None:
        from backend.agent.thinking_effort import normalize_thinking_effort

        conv.thinking_effort = normalize_thinking_effort(thinking_effort)
    if title is not None:
        text = title.strip()
        if text:
            conv.title = text[:120]
    if disabled_packs is not None or enabled_subskills is not None:
        next_disabled = (
            disabled_packs
            if disabled_packs is not None
            else list(getattr(conv, "disabled_packs", None) or [])
        )
        next_subs = (
            enabled_subskills
            if enabled_subskills is not None
            else (getattr(conv, "enabled_subskills", None) or {})
        )
        set_conversation_skill_selection(
            conv_id,
            enabled_packs=[],  # unused — disabled_packs wins via helper below
            enabled_subskills=next_subs if isinstance(next_subs, dict) else {},
            project_root=project_root,
            disabled_packs=next_disabled,
        )
        conv = load_conversation(conv_id, project_root)
        if not conv:
            return
    if disabled_tool_ids is not None:
        _apply_disabled_tool_ids_to_conv(conv, disabled_tool_ids)
    from backend.agent.prompt_cache import invalidate_conv_cache

    invalidate_conv_cache(conv)
    conv.settings_edited = time.time()
    _saves.save_conversation(conv, project_root, touch_updated=False)


def ensure_conversation_file_path(
    conv_id: str,
    file_path: str,
    project_root: str | None = None,
) -> None:
    path = norm_file_path(file_path)
    if not path:
        return
    conv = load_conversation(conv_id, project_root)
    if not conv or conv.file_path:
        return
    conv.file_path = path
    _saves.save_conversation(conv, project_root, touch_updated=False)


def set_conversation_enabled_skills(
    conv_id: str,
    filenames: list[str],
    project_root: str | None = None,
) -> list[str]:
    """Legacy: treat filenames as an allowlist → convert to deny complementary (unused)."""
    del filenames
    packs = set_conversation_disabled_packs(conv_id, [], project_root=project_root)
    return packs


def set_conversation_disabled_packs(
    conv_id: str,
    disabled_packs: list[str],
    project_root: str | None = None,
) -> list[str]:
    conv = load_conversation(conv_id, project_root)
    existing_subs = (
        dict(conv.enabled_subskills)
        if conv and isinstance(getattr(conv, "enabled_subskills", None), dict)
        else {}
    )
    _packs, _subs = set_conversation_skill_selection(
        conv_id,
        enabled_packs=[],
        enabled_subskills=existing_subs,
        project_root=project_root,
        disabled_packs=disabled_packs,
    )
    del _packs, _subs
    from backend.skills.selection import merge_selection

    return merge_selection(disabled_packs=disabled_packs).disabled_packs


def set_conversation_skill_selection(
    conv_id: str,
    enabled_packs: list[str],
    enabled_subskills: dict[str, list[str]],
    project_root: str | None = None,
    *,
    disabled_packs: list[str] | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    """Persist pack deny-list + optional per-pack subskill allowlists."""
    from backend.skills.selection import merge_selection
    from backend.skills.skill_index import conversation_skill_text

    conv = load_conversation(conv_id, project_root)
    if not conv:
        return [], {}
    if disabled_packs is None:
        sel = merge_selection(
            enabled_packs=enabled_packs,
            enabled_subskills=enabled_subskills,
        )
    else:
        sel = merge_selection(
            disabled_packs=disabled_packs,
            enabled_subskills=enabled_subskills,
        )
    conv.disabled_packs = list(sel.disabled_packs)
    conv.enabled_packs = None
    conv.enabled_subskills = dict(sel.enabled_subskills) or None
    conv.enabled_skills = None
    conv.skill_snapshot = conversation_skill_text(
        disabled_packs=sel.disabled_packs,
        enabled_subskills=sel.enabled_subskills,
        skill_snapshot="",
        mutate=False,
    )
    from backend.skills.revision import skills_revision

    conv.skill_snapshot_rev = skills_revision()
    from backend.agent.prompt_cache import invalidate_conv_cache

    invalidate_conv_cache(conv)
    _saves.save_conversation(conv, project_root, touch_updated=False)
    return sel.enabled_packs, sel.enabled_subskills


def invalidate_all_conversation_caches(project_root: str | None = None) -> None:
    """Clear frozen prompt snapshots for every chat in the project."""
    from backend.agent.prompt_cache import invalidate_conv_cache

    for conv in _load_all_conversations(project_root, include_messages=not _store._use_db()):
        invalidate_conv_cache(conv)
        _saves.save_conversation(conv, project_root, touch_updated=False)


def set_conversation_ducky_style(
    conv_id: str,
    ducky_style: str,
    ducky_personality: str | None = None,
    project_root: str | None = None,
) -> None:
    conv = load_conversation(conv_id, project_root)
    if conv:
        conv.ducky_style = normalize_ducky_style(ducky_style)
        if ducky_personality is not None:
            conv.ducky_personality = (ducky_personality or "").strip()
        from backend.agent.prompt_cache import invalidate_conv_cache

        invalidate_conv_cache(conv)
        _saves.save_conversation(conv, project_root, touch_updated=False)


def delete_conversation(conv_id: str, project_root: str | None = None) -> None:
    from frontend.ui_web.conversation_attachments import conversation_dir

    # A spawned chat is owned by its parent. Permanently deleting the parent must
    # not leave orphaned sub-agent conversations behind.
    ids = [conv_id, *_groups.conversation_descendant_ids(conv_id, project_root)]
    try:
        from backend.tools.panel.permission_prompt import forget_chat
    except Exception:
        forget_chat = None
    for target_id in reversed(ids):
        if _store._use_db():
            _saves.flush_chat_saves(target_id)
            _store._repo().conv_delete(target_id)
            _saves._forget_saved(target_id)
        if forget_chat is not None:
            forget_chat(target_id)  # its "Allow everything" must not outlive it
        try:
            from backend.sandbox import paths as sandbox_paths

            if sandbox_paths.exists(target_id):
                from backend.sandbox import service as sandbox_service

                sandbox_service.clear(target_id)  # its sandbox, programs and worktrees go too
        except Exception:
            pass
        conv_folder = conversation_dir(target_id, project_root, _store._conversations_dir(project_root))
        if not conv_folder.is_dir():
            meta = _find_conversation_meta(target_id, project_root)
            if meta is not None:
                conv_folder = meta.parent
        if conv_folder.is_dir():
            try:
                shutil.rmtree(conv_folder)
            except OSError:
                pass
        _store.release_conversation_lock(target_id)


# The sidebar auto-numbers fresh duckies ("NewDucky1", "New ducky2"), so the
# trailing digits have to count as unnamed too.
_PLACEHOLDER_RE = re.compile(
    r"^(new\s*ducky|new\s*chat|ducky|chat|group|sub-?agent)\s*\d*$", re.IGNORECASE
)


def is_placeholder_title(title: str) -> bool:
    """True when the chat still carries an auto-generated name the user never chose."""
    text = (title or "").strip()
    return not text or bool(_PLACEHOLDER_RE.match(text))


def auto_title(conv: Conversation, title: str, project_root: str | None = None) -> bool:
    """Apply ``title`` only while the chat still has a placeholder name. Returns True when set."""
    if not is_placeholder_title(conv.title):
        # Named spawn / invite / rename already set a real title — keep it.
        return False
    text = (title or "").strip().replace("\n", " ")
    if not text:
        return False
    conv.title = text[:60] + ("…" if len(text) > 60 else "")
    _saves.save_conversation(conv, project_root)
    return True


def retitle_if_unchanged(
    conv_id: str, expected: str, title: str, project_root: str | None = None
) -> bool:
    """Compare-and-swap the title so a background rename never overwrites a manual one."""
    text = (title or "").strip()
    if not text:
        return False
    with _store._conversation_lock(conv_id):
        fresh = load_conversation(conv_id, project_root)
        if fresh is None or (fresh.title or "").strip() != expected:
            return False
        fresh.title = text[:120]
        _saves.save_conversation(fresh, project_root, touch_updated=False)
    return True


def rename_conversation(conv_id: str, title: str, project_root: str | None = None) -> None:
    conv = load_conversation(conv_id, project_root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    text = title.strip()
    if not text:
        raise ValueError("title must not be empty")
    conv.title = text[:120]
    _saves.save_conversation(conv, project_root)


def move_conversation(conv_id: str, folder_id: str, project_root: str | None = None) -> None:
    conv = load_conversation(conv_id, project_root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    if not _folders._folder_is_known(folder_id, project_root):
        raise ValueError(f"Unknown folder: {folder_id!r}")
    target_folder = folder_id or ""
    archiving = is_archive_folder_id(target_folder)
    restoring = is_archive_folder_id(conv.folder_id) and not archiving
    if getattr(conv, "is_group", False):
        # A group goes to Archive and comes back whole, with its duckies and nested groups.
        if restoring:
            _groups.restore_archived_group(conv_id, project_root, target_folder)
            return
        if archiving:
            own = next(
                (f for f in _folders.load_folders(project_root) if (getattr(f, "group_hub_id", None) or "") == conv_id),
                None,
            )
            if own is not None:
                _folders.delete_folder(own.id, project_root, archive_members=True)
                return
    if restoring and (conv.parent_conv_id or "").strip():
        parent = load_conversation(conv.parent_conv_id.strip(), project_root)
        if parent is not None and getattr(parent, "is_group", False) and is_archive_folder_id(parent.folder_id):
            conv.parent_conv_id = ""  # out of an archived group: a ducky on its own
    # Archive/restore the complete owned sub-agent tree. Otherwise children become
    # orphaned top-level chats and archived agents remain addressable.
    cascade = archiving or restoring
    ids = [conv_id, *_groups.conversation_descendant_ids(conv_id, project_root)] if cascade else [conv_id]
    by_id = {c.id: c for c in _load_all_conversations(project_root, include_messages=not _store._use_db())}
    by_id[conv.id] = conv
    for target_id in ids:
        target = by_id.get(target_id)
        if target is None:
            continue
        target.folder_id = target_folder
        _saves.save_conversation(target, project_root)
