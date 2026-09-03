"""Project-scoped chat folders and conversations (wraps paths; does not modify chat_store)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from frontend.archive_folder import (
    ARCHIVE_FOLDER_ID,
    assert_not_archive_folder,
    ensure_archive_folder,
    is_archive_folder_id,
)
from frontend.atomic_json import write_json_atomic
from frontend.chat_store import (
    ChatFolder,
    Conversation,
    norm_file_path,
    normalize_ducky_style,
    remap_conversation_file_path,
)
from frontend.settings import PanelSettings, default_app_data_dir

_migrate_lock_guard = threading.Lock()
_migrate_locks: dict[str, threading.Lock] = {}
_conv_lock_guard = threading.Lock()
_conv_locks: dict[str, threading.RLock] = {}
_sort_migrated_slugs: set[str] = set()


def release_conversation_lock(conv_id: str) -> None:
    """Drop the per-conv lock after delete so the dict does not grow forever."""
    with _conv_lock_guard:
        _conv_locks.pop(conv_id, None)


def _conversation_lock(conv_id: str) -> threading.RLock:
    with _conv_lock_guard:
        lock = _conv_locks.get(conv_id)
        if lock is None:
            lock = threading.RLock()
            _conv_locks[conv_id] = lock
        return lock


def _conversation_migrate_lock(project_root: str | None) -> threading.Lock:
    root = project_root
    if root is None:
        root = PanelSettings.load().uefn_project_root
    slug = project_slug(root)
    with _migrate_lock_guard:
        lock = _migrate_locks.get(slug)
        if lock is None:
            lock = threading.Lock()
            _migrate_locks[slug] = lock
        return lock


def project_slug(project_root: str) -> str:
    raw = (project_root or "").strip()
    if not raw:
        return "_no_project"
    try:
        resolved = str(Path(raw).resolve())
    except OSError:
        resolved = raw
    digest = hashlib.sha256(resolved.lower().encode("utf-8")).hexdigest()[:16]
    name = Path(raw).name or "project"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
    return f"{safe}_{digest}"


def project_display_name(project_root: str) -> str:
    raw = (project_root or "").strip()
    if not raw:
        return "No project"
    return Path(raw).name or raw


def _chats_root() -> Path:
    return default_app_data_dir() / "chats" / "projects"


def _project_root(project_root: str | None = None) -> Path:
    root = project_root
    if root is None:
        root = PanelSettings.load().uefn_project_root
    slug = project_slug(root)
    d = _chats_root() / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def _folders_path(project_root: str | None = None) -> Path:
    return _project_root(project_root) / "folders.json"


def _conversations_dir(project_root: str | None = None) -> Path:
    d = _project_root(project_root) / "conversations"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_conversations_dir(project_root: str | None = None) -> Path:
    return _conversations_dir(project_root)


def _default_folders() -> list[dict[str, Any]]:
    return []


def _folder_sort_key(folder: ChatFolder) -> tuple[str, float, str]:
    return (folder.parent_id or "", folder.sort_order, folder.id)


def _sort_folders(folders: list[ChatFolder]) -> list[ChatFolder]:
    return sorted(folders, key=_folder_sort_key)


def _conversation_sort_key(conv: Conversation) -> tuple[float, float, str]:
    return (conv.sort_order, -conv.updated, conv.id)


def _sort_conversations(convs: list[Conversation]) -> list[Conversation]:
    return sorted(convs, key=_conversation_sort_key)


def _migrate_folder_sort_orders(folders: list[ChatFolder]) -> bool:
    """Assign sort_order from sibling index when every sibling in a group is still 0."""
    changed = False
    by_parent: dict[str, list[ChatFolder]] = {}
    for folder in folders:
        by_parent.setdefault(folder.parent_id or "", []).append(folder)
    for group in by_parent.values():
        if len(group) <= 1:
            continue
        if all(f.sort_order == 0.0 for f in group):
            for idx, folder in enumerate(group):
                folder.sort_order = float(idx)
                changed = True
    return changed


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

    out: list[Conversation] = []
    seen: set[str] = set()
    conv_dir = _conversations_dir(project_root)
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
    root = project_root
    if root is None:
        root = PanelSettings.load().uefn_project_root
    slug = project_slug(root)
    if slug in _sort_migrated_slugs:
        return
    with _conversation_migrate_lock(project_root):
        if slug in _sort_migrated_slugs:
            return
        all_convs = _load_all_conversations(project_root, include_messages=True)
        for conv in _migrate_conversation_sort_orders(all_convs):
            save_conversation(conv, project_root, touch_updated=False)
        _sort_migrated_slugs.add(slug)


def _folder_by_id(folders: list[ChatFolder]) -> dict[str, ChatFolder]:
    return {f.id: f for f in folders}


def _would_create_cycle(folders: list[ChatFolder], folder_id: str, new_parent_id: str) -> bool:
    if not new_parent_id or folder_id == new_parent_id:
        return folder_id == new_parent_id and folder_id != ""
    by_id = _folder_by_id(folders)
    current = new_parent_id
    while current:
        if current == folder_id:
            return True
        parent = by_id.get(current)
        if parent is None:
            break
        current = parent.parent_id or ""
    return False


def load_folders(project_root: str | None = None) -> list[ChatFolder]:
    path = _folders_path(project_root)
    if not path.is_file():
        write_json_atomic(path, {"folders": _default_folders()})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("folders") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            return []
        folders = [ChatFolder.from_dict(f) for f in raw if isinstance(f, dict)]
        folders, archive_added = ensure_archive_folder(folders)
        if archive_added or _migrate_folder_sort_orders(folders):
            save_folders(folders, project_root)
        return _sort_folders(folders)
    except (json.JSONDecodeError, OSError):
        return []


def save_folders(folders: list[ChatFolder], project_root: str | None = None) -> None:
    write_json_atomic(_folders_path(project_root), {"folders": [f.to_dict() for f in folders]})


def create_folder(name: str, parent_id: str = "", project_root: str | None = None) -> ChatFolder:
    if is_archive_folder_id(parent_id):
        raise ValueError("Cannot create folders inside Archive")
    folders = load_folders(project_root)
    parent_key = parent_id or ""
    siblings = [f for f in folders if (f.parent_id or "") == parent_key]
    next_order = max((f.sort_order for f in siblings), default=-1.0) + 1.0
    folder = ChatFolder(
        id=str(uuid.uuid4()),
        name=name.strip() or "Folder",
        parent_id=parent_id,
        sort_order=next_order,
    )
    folders.append(folder)
    save_folders(folders, project_root)
    return folder


def rename_folder(folder_id: str, name: str, project_root: str | None = None) -> None:
    assert_not_archive_folder(folder_id, action="rename")
    folders = load_folders(project_root)
    hub_id = ""
    new_name = ""
    for folder in folders:
        if folder.id == folder_id:
            folder.name = name.strip() or folder.name
            new_name = folder.name
            hub_id = (getattr(folder, "group_hub_id", None) or "").strip()
            break
    save_folders(folders, project_root)
    # Group folders share their name with the hub chat.
    if hub_id and new_name:
        hub = load_conversation(hub_id, project_root=project_root)
        if hub is not None and getattr(hub, "is_group", False):
            hub.title = new_name
            save_conversation(hub, project_root)


def ensure_group_folder_hubs(project_root: str | None = None) -> int:
    """Wrap legacy is_group chats in a folder hub so the sidebar shows one Group, not chat+folder."""
    folders = load_folders(project_root)
    linked = {(getattr(f, "group_hub_id", None) or "").strip() for f in folders}
    linked.discard("")
    migrated = 0
    # Full load: list_conversations() strips messages, and these get saved back.
    for conv in _load_all_conversations(project_root):
        if not getattr(conv, "is_group", False):
            continue
        if conv.id in linked:
            continue
        parent = (conv.folder_id or "").strip()
        # An unlinked hub in Archive is a tombstone from an older delete, not a
        # legacy group. Wrapping it in a folder resurrected deleted groups at the
        # root on the very next sidebar load.
        if is_archive_folder_id(parent):
            continue
        hub_folder = create_folder(conv.title or "Group", parent, project_root)
        conv.folder_id = hub_folder.id
        save_conversation(conv, project_root)
        folders = load_folders(project_root)
        for folder in folders:
            if folder.id == hub_folder.id:
                folder.group_hub_id = conv.id
                break
        save_folders(folders, project_root)
        linked.add(conv.id)
        migrated += 1
    return migrated


def folder_subtree_ids(folder_id: str, project_root: str | None = None) -> list[str]:
    """The folder plus every folder nested under it, parents before children."""
    folders = load_folders(project_root)
    if not any(f.id == folder_id for f in folders):
        return []
    children: dict[str, list[str]] = {}
    for folder in folders:
        children.setdefault(folder.parent_id or "", []).append(folder.id)
    out: list[str] = []
    seen: set[str] = set()
    queue = [folder_id]
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        out.append(current)
        queue.extend(children.get(current, []))
    return out


def group_hub_ids_in(folder_ids: list[str], project_root: str | None = None) -> list[str]:
    """Hub conversation ids for whichever of these folders are groups."""
    wanted = set(folder_ids)
    out: list[str] = []
    for folder in load_folders(project_root):
        if folder.id not in wanted:
            continue
        hub_id = (getattr(folder, "group_hub_id", None) or "").strip()
        if hub_id:
            out.append(hub_id)
    return out


def delete_folder(folder_id: str, project_root: str | None = None) -> list[str]:
    """Delete a folder. Returns the hub conversation ids that went away with it."""
    assert_not_archive_folder(folder_id, action="delete")
    folders = load_folders(project_root)
    target = next((f for f in folders if f.id == folder_id), None)
    if target is None:
        return []

    if not (getattr(target, "group_hub_id", None) or "").strip():
        # Plain folder: its contents surface at the root rather than being deleted.
        for folder in folders:
            if folder.parent_id == folder_id:
                folder.parent_id = ""
        save_folders([f for f in folders if f.id != folder_id], project_root)
        # Full load: list_conversations() strips messages, so saving those back
        # would blank the chat history.
        for conv in _load_all_conversations(project_root):
            if conv.folder_id != folder_id:
                continue
            conv.folder_id = ""
            save_conversation(conv, project_root)
        return []

    # A group folder *is* the group, so deleting it takes the whole subtree with
    # it — nested groups used to survive by being re-parented to the root.
    doomed_ids = set(folder_subtree_ids(folder_id, project_root))
    hub_ids = set(group_hub_ids_in(sorted(doomed_ids), project_root))
    save_folders([f for f in folders if f.id not in doomed_ids], project_root)

    all_convs = _load_all_conversations(project_root)
    by_parent: dict[str, list[Conversation]] = {}
    for conv in all_convs:
        by_parent.setdefault((conv.parent_conv_id or "").strip(), []).append(conv)

    members: list[Conversation] = []
    seen = set(hub_ids)
    queue = [
        conv
        for conv in all_convs
        if conv.id not in hub_ids
        and (conv.folder_id in doomed_ids or (conv.parent_conv_id or "").strip() in hub_ids)
    ]
    while queue:
        conv = queue.pop()
        if conv.id in seen:
            continue
        seen.add(conv.id)
        members.append(conv)
        queue.extend(by_parent.get(conv.id, []))

    # Members outlive their group as plain archived duckies. Unlink them from the
    # hub first: delete_conversation() follows parent_conv_id and would wipe them.
    for conv in members:
        if (conv.parent_conv_id or "").strip() in hub_ids:
            conv.parent_conv_id = ""
        conv.leader_conv_id = ""
        conv.folder_id = ARCHIVE_FOLDER_ID
        save_conversation(conv, project_root)

    # The hub is the group itself, not a ducky — archiving it only left a ghost
    # "Group1" row sitting in Archive forever.
    for hub_id in hub_ids:
        delete_conversation(hub_id, project_root)
    return sorted(hub_ids)


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
    for conv in _load_all_conversations(project_root):
        next_path = remap_conversation_file_path(conv.file_path, from_path, to_path)
        if next_path != norm_file_path(conv.file_path):
            conv.file_path = next_path
            save_conversation(conv, project_root, touch_updated=False)
            changed += 1
    return changed


def conversation_path(conv_id: str, project_root: str | None = None) -> Path:
    """Primary save path: folder layout conversation.json."""
    from frontend.ui_web.conversation_attachments import conversation_meta_path

    return conversation_meta_path(conv_id, _conversations_dir(project_root))


def load_conversation(conv_id: str, project_root: str | None = None) -> Conversation | None:
    meta = conversation_path(conv_id, project_root)
    if not meta.is_file():
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return Conversation.from_dict(data)
    except (json.JSONDecodeError, OSError):
        pass
    return None


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
    from backend.skills.store import (
        build_skill_prompt,
        resolve_conversation_selection,
        skills_revision,
    )

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
    save_conversation(conv, project_root, touch_updated=False)
    return changed


def save_conversation(conv: Conversation, project_root: str | None = None, *, touch_updated: bool = True) -> None:
    with _conversation_lock(conv.id):
        if touch_updated:
            conv.updated = time.time()
        path = conversation_path(conv.id, project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        data = conv.to_dict()
        write_json_atomic(path, data)
        try:
            from frontend.perf_trace import trace

            payload = json.dumps(data, ensure_ascii=False, default=str)
            trace(
                "conv_save",
                conv.id,
                (time.perf_counter() - t0) * 1000.0,
                bytes=len(payload),
                message_count=len(getattr(conv, "messages", []) or []),
            )
        except Exception:
            pass


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
) -> Conversation:
    del enabled_packs, enabled_skills, tool_ids  # legacy pack/tool allowlists ignored
    now = time.time()
    s = settings or PanelSettings.load()
    root = project_root if project_root is not None else s.uefn_project_root
    from backend.skills.store import conversation_skill_text, merge_selection, skills_revision
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
        id=str(uuid.uuid4()),
        folder_id=folder_id or "",
        title=(title.strip()[:120] if title.strip() else "New ducky"),
        created=now,
        updated=now,
        sort_order=max(
            (c.sort_order for c in list_conversations(folder_id or "", root)),
            default=-1.0,
        )
        + 1.0,
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
    save_conversation(conv, root)
    return conv


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
    root = _chats_root()
    if not root.is_dir():
        return []
    return list(root.glob("*/conversations/*/conversation.json"))


def opt_in_uefn_plugin_all_chats(plugin_id: str) -> int:
    """Add ``plugin_id`` to every chat with an explicit uefn_plugins allowlist."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    n = 0
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
    save_conversation(conv, project_root, touch_updated=False)


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
    save_conversation(conv, project_root, touch_updated=False)


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
    from backend.skills.store import merge_selection

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
    from backend.skills.store import conversation_skill_text, merge_selection

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
    from backend.skills.store import skills_revision

    conv.skill_snapshot_rev = skills_revision()
    from backend.agent.prompt_cache import invalidate_conv_cache

    invalidate_conv_cache(conv)
    save_conversation(conv, project_root, touch_updated=False)
    return sel.enabled_packs, sel.enabled_subskills


def invalidate_all_conversation_caches(project_root: str | None = None) -> None:
    """Clear frozen prompt snapshots for every chat in the project."""
    from backend.agent.prompt_cache import invalidate_conv_cache

    for conv in _load_all_conversations(project_root):
        invalidate_conv_cache(conv)
        save_conversation(conv, project_root, touch_updated=False)


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
        save_conversation(conv, project_root, touch_updated=False)


def conversation_descendant_ids(conv_id: str, project_root: str | None = None) -> list[str]:
    """Return every persisted child/grandchild chat, cycle-safe and parent-first."""
    children: dict[str, list[str]] = {}
    for conv in _load_all_conversations(project_root):
        parent = (conv.parent_conv_id or "").strip()
        if parent:
            children.setdefault(parent, []).append(conv.id)
    out: list[str] = []
    seen = {conv_id}
    queue = list(children.get(conv_id, []))
    while queue:
        child_id = queue.pop(0)
        if child_id in seen:
            continue
        seen.add(child_id)
        out.append(child_id)
        queue.extend(children.get(child_id, []))
    return out


def delete_conversation(conv_id: str, project_root: str | None = None) -> None:
    from frontend.ui_web.conversation_attachments import conversation_dir

    # A spawned chat is owned by its parent. Permanently deleting the parent must
    # not leave orphaned sub-agent conversations behind.
    ids = [conv_id, *conversation_descendant_ids(conv_id, project_root)]
    for target_id in reversed(ids):
        conv_folder = conversation_dir(target_id, project_root, _conversations_dir(project_root))
        if conv_folder.is_dir():
            try:
                shutil.rmtree(conv_folder)
            except OSError:
                pass
        release_conversation_lock(target_id)


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
    save_conversation(conv, project_root)
    return True


def retitle_if_unchanged(
    conv_id: str, expected: str, title: str, project_root: str | None = None
) -> bool:
    """Compare-and-swap the title so a background rename never overwrites a manual one."""
    text = (title or "").strip()
    if not text:
        return False
    with _conversation_lock(conv_id):
        fresh = load_conversation(conv_id, project_root)
        if fresh is None or (fresh.title or "").strip() != expected:
            return False
        fresh.title = text[:120]
        save_conversation(fresh, project_root, touch_updated=False)
    return True


def append_message(conv: Conversation, message: dict[str, Any], project_root: str | None = None) -> None:
    with _conversation_lock(conv.id):
        fresh = load_conversation(conv.id, project_root)
        if fresh is not None:
            conv.messages = list(fresh.messages)
            conv.updated = fresh.updated
        conv.messages.append(message)
        save_conversation(conv, project_root, touch_updated=True)


def rename_conversation(conv_id: str, title: str, project_root: str | None = None) -> None:
    conv = load_conversation(conv_id, project_root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    text = title.strip()
    if not text:
        raise ValueError("title must not be empty")
    conv.title = text[:120]
    save_conversation(conv, project_root)


def move_conversation(conv_id: str, folder_id: str, project_root: str | None = None) -> None:
    conv = load_conversation(conv_id, project_root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    folders = load_folders(project_root)
    if folder_id and folder_id not in {f.id for f in folders}:
        raise ValueError(f"Unknown folder: {folder_id!r}")
    target_folder = folder_id or ""
    # Archive/restore the complete owned sub-agent tree. Otherwise children become
    # orphaned top-level chats and archived agents remain addressable.
    cascade = is_archive_folder_id(target_folder) or is_archive_folder_id(conv.folder_id)
    ids = [conv_id, *conversation_descendant_ids(conv_id, project_root)] if cascade else [conv_id]
    by_id = {c.id: c for c in _load_all_conversations(project_root)}
    by_id[conv.id] = conv
    for target_id in ids:
        target = by_id.get(target_id)
        if target is None:
            continue
        target.folder_id = target_folder
        save_conversation(target, project_root)


def apply_sidebar_layout(
    *,
    folders: list[dict[str, Any]],
    chats: list[dict[str, Any]],
    project_root: str | None = None,
) -> None:
    """Persist folder tree order/nesting and chat positions."""
    current_folders = load_folders(project_root)
    by_folder_id = _folder_by_id(current_folders)

    folder_updates = {str(row.get("id", "")): row for row in folders if isinstance(row, dict) and row.get("id")}
    for folder_id, row in folder_updates.items():
        if folder_id not in by_folder_id:
            raise ValueError(f"Unknown folder: {folder_id}")
        if is_archive_folder_id(folder_id):
            raise ValueError("Cannot reorder or reparent the Archive folder")

    for folder in current_folders:
        if is_archive_folder_id(folder.id):
            continue
        row = folder_updates.get(folder.id)
        if not row:
            continue
        parent_id = str(row.get("parent_id", "") or "")
        if is_archive_folder_id(parent_id):
            raise ValueError("Cannot nest folders under Archive")
        if parent_id and parent_id not in by_folder_id:
            raise ValueError(f"Unknown parent folder: {parent_id}")
        if _would_create_cycle(current_folders, folder.id, parent_id):
            raise ValueError(f"Cannot reparent {folder.id}: cycle detected")
        folder.parent_id = parent_id
        folder.sort_order = float(row.get("sort_order", folder.sort_order))

    save_folders(current_folders, project_root)

    chat_updates = {str(row.get("id", "")): row for row in chats if isinstance(row, dict) and row.get("id")}
    if not chat_updates:
        return

    all_convs = _load_all_conversations(project_root)
    by_conv_id = {c.id: c for c in all_convs}
    for conv_id, row in chat_updates.items():
        conv = by_conv_id.get(conv_id)
        if conv is None:
            raise ValueError(f"Unknown conversation: {conv_id}")
        folder_id = str(row.get("folder_id", conv.folder_id) or "")
        if folder_id and folder_id not in by_folder_id:
            raise ValueError(f"Unknown folder for chat: {folder_id}")
        conv.folder_id = folder_id
        conv.sort_order = float(row.get("sort_order", conv.sort_order))
        save_conversation(conv, project_root, touch_updated=False)
