"""Persistent chat folders and conversations in AppData."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from frontend.atomic_json import write_json_atomic
from frontend.ducky_assets import normalize_ducky_style
from frontend.settings import PanelSettings, default_app_data_dir


def chats_root() -> Path:
    return default_app_data_dir() / "chats"


def conversations_dir() -> Path:
    d = chats_root() / "conversations"
    d.mkdir(parents=True, exist_ok=True)
    return d


def folders_path() -> Path:
    return chats_root() / "folders.json"


def norm_file_path(path: str) -> str:
    return (path or "").strip().replace("\\", "/")


@dataclass
class ChatFolder:
    id: str
    name: str
    parent_id: str = ""
    sort_order: float = 0.0
    group_hub_id: str = ""
    """When set, this folder IS a group: clicking it opens the group hub chat."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "parent_id": self.parent_id,
            "sort_order": self.sort_order,
            "group_hub_id": (self.group_hub_id or "").strip(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ChatFolder:
        raw_sort = d.get("sort_order")
        sort_order = float(raw_sort) if raw_sort is not None and raw_sort != "" else 0.0
        return cls(
            id=str(d.get("id", "")),
            name=str(d.get("name", "Folder")),
            parent_id=str(d.get("parent_id", "") or ""),
            sort_order=sort_order,
            group_hub_id=str(d.get("group_hub_id", "") or "").strip(),
        )


@dataclass
class Conversation:
    id: str
    folder_id: str = ""
    title: str = "New ducky"
    created: float = 0.0
    updated: float = 0.0
    sort_order: float = 0.0
    provider: str = "anthropic"
    model: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    skill_snapshot: str = ""
    skill_snapshot_rev: str = ""
    """Skill-index revision the snapshot was built from (see skills_revision)."""
    enabled_skills: list[str] | None = None
    """Legacy pack ids stored as filenames."""
    enabled_packs: list[str] | None = None
    enabled_subskills: dict[str, list[str]] | None = None
    disabled_packs: list[str] | None = None
    """Skill pack ids denied for this ducky (None/empty = all packs available, lazy-loaded)."""
    ducky_style: str = "classic"
    ducky_name: str = ""
    """Personality identity name (separate from sidebar chat title)."""
    profile_id: str = ""
    """Stable agent-profile id this chat was created from. Prefer over ducky_name for identity."""
    ducky_personality: str = ""
    """Per-ducky personality and response-style instructions (extra system context)."""
    tts_voice: str = ""
    """Per-ducky TTS voice id (builtin:<name> or plugin:<pid>:<id>); empty = global default."""
    tts_speed: float = 0.0
    """Per-ducky talking-speed multiplier; 0 = use the global default speed."""
    file_path: str = ""
    """Workspace-relative file this ducky is scoped to (empty = not file-bound)."""
    context_omit: list[str] | None = None
    """Persisted context segment omissions: system, tools, rules, skill, mcp."""
    context_summary: str = ""
    """Rolling LLM digest of older turns — prompt shrink only; messages stay intact."""
    context_summary_through: int = 0
    """Index into messages covered by context_summary (exclusive end)."""
    context_summary_tokens: int = 0
    """Estimated tokens of the summary block (UI / thresholds)."""
    mcp_plugins: list[str] | None = None
    """Per-chat MCP plugin ids override (None = follow global settings)."""
    builtin_toolsets: list[str] | None = None
    """Per-chat enabled built-in tool group ids (None = follow global settings)."""
    uefn_plugins: list[str] | None = None
    """Per-chat UEFN app-plugin tool allowlist (None = all Store-enabled; [] = none)."""
    token_usage: dict[str, Any] | None = None
    """Exact API token log: total_input, total_output, calls[]."""
    prompt_cache_snapshot: dict[str, Any] | None = None
    """Frozen cacheable system-prompt blocks + tool names for prompt caching."""

    coding_agent: str = "ducky"
    """Coding-agent path: ducky | claude_code | codex | cursor."""

    thinking_effort: str = ""
    """Anthropic extended thinking: ''|off|low|medium|high (empty = off)."""

    upstream_session_id: str = ""
    """Upstream CLI/SDK session id when using an external coding agent."""

    coding_agent_stats: dict[str, Any] | None = None
    """Latest real usage snapshot from the external agent's last turn:
    model, context_tokens, num_turns, cost_usd, updated. UI-only."""

    terminal_session_id: str = ""
    """Linked panel terminal session for CLI coding agents."""

    parent_conv_id: str = ""
    """Group hub id when this chat is a swarm member (empty = top-level).
    Parent-linked subagent seats are no longer created."""

    is_group: bool = False
    """True when this conversation is a multi-ducky group chat tab."""

    leader_conv_id: str = ""
    """Group hub only: member_conv_id of the designated leader (empty = first leaf)."""

    group_members: list[dict[str, Any]] = field(default_factory=list)
    """Group roster: [{member_conv_id, profile_id, name, tts_voice, color}]. Empty for normal chats."""

    tool_call_count: int = 0
    """Sidebar aggregate: total tool_call blocks across messages."""

    file_count: int = 0
    """Sidebar aggregate: unique session files touched by write/path tools."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "folder_id": self.folder_id,
            "title": self.title,
            "created": self.created,
            "updated": self.updated,
            "sort_order": self.sort_order,
            "provider": self.provider,
            "model": self.model,
            "messages": self.messages,
            "skill_snapshot": self.skill_snapshot,
            "skill_snapshot_rev": self.skill_snapshot_rev,
            "enabled_skills": self.enabled_skills,
            "enabled_packs": self.enabled_packs,
            "enabled_subskills": self.enabled_subskills,
            "disabled_packs": self.disabled_packs,
            "ducky_style": normalize_ducky_style(self.ducky_style),
            "ducky_name": (self.ducky_name or "").strip(),
            "profile_id": (self.profile_id or "").strip(),
            "ducky_personality": (self.ducky_personality or "").strip(),
            "tts_voice": (self.tts_voice or "").strip(),
            "tts_speed": float(self.tts_speed or 0.0),
            "file_path": norm_file_path(self.file_path),
            "context_omit": self.context_omit,
            "context_summary": self.context_summary or "",
            "context_summary_through": int(self.context_summary_through or 0),
            "context_summary_tokens": int(self.context_summary_tokens or 0),
            "mcp_plugins": self.mcp_plugins,
            "builtin_toolsets": self.builtin_toolsets,
            "uefn_plugins": self.uefn_plugins,
            "token_usage": self.token_usage,
            "prompt_cache_snapshot": self.prompt_cache_snapshot,
            "coding_agent": (self.coding_agent or "ducky").strip() or "ducky",
            "thinking_effort": (self.thinking_effort or "").strip().lower(),
            "upstream_session_id": (self.upstream_session_id or "").strip(),
            "coding_agent_stats": self.coding_agent_stats,
            "terminal_session_id": (self.terminal_session_id or "").strip(),
            "parent_conv_id": (self.parent_conv_id or "").strip(),
            "is_group": bool(self.is_group),
            "leader_conv_id": (self.leader_conv_id or "").strip(),
            "group_members": list(self.group_members or []),
            "tool_call_count": int(self.tool_call_count or 0),
            "file_count": int(self.file_count or 0),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Conversation:
        raw_sort = d.get("sort_order")
        sort_order = float(raw_sort) if raw_sort is not None and raw_sort != "" else 0.0
        return cls(
            id=str(d.get("id", "")),
            folder_id=str(d.get("folder_id", "") or ""),
            title=str(d.get("title", "New ducky")),
            created=float(d.get("created", 0) or 0),
            updated=float(d.get("updated", 0) or 0),
            sort_order=sort_order,
            provider=str(d.get("provider", "anthropic")),
            model=str(d.get("model", "") or ""),
            messages=list(d.get("messages") or []),
            skill_snapshot=str(d.get("skill_snapshot", "") or ""),
            skill_snapshot_rev=str(d.get("skill_snapshot_rev", "") or ""),
            enabled_skills=(
                [str(x) for x in d["enabled_skills"]]
                if "enabled_skills" in d and d["enabled_skills"] is not None
                else None
            ),
            enabled_packs=(
                [str(x) for x in d["enabled_packs"]]
                if "enabled_packs" in d and d["enabled_packs"] is not None
                else None
            ),
            enabled_subskills=(
                {str(k): [str(x) for x in v] for k, v in d["enabled_subskills"].items()}
                if "enabled_subskills" in d
                and isinstance(d["enabled_subskills"], dict)
                else None
            ),
            disabled_packs=(
                [str(x) for x in d["disabled_packs"]]
                if "disabled_packs" in d and d["disabled_packs"] is not None
                else None
            ),
            ducky_style=normalize_ducky_style(str(d.get("ducky_style", "") or "")),
            ducky_name=str(d.get("ducky_name", "") or ""),
            profile_id=str(d.get("profile_id", "") or "").strip(),
            ducky_personality=str(d.get("ducky_personality", "") or ""),
            tts_voice=str(d.get("tts_voice", "") or "").strip(),
            tts_speed=float(d.get("tts_speed", 0) or 0.0),
            file_path=norm_file_path(str(d.get("file_path", "") or "")),
            context_omit=(
                [str(x) for x in d["context_omit"]]
                if "context_omit" in d and d["context_omit"] is not None
                else None
            ),
            context_summary=str(d.get("context_summary", "") or ""),
            context_summary_through=int(d.get("context_summary_through", 0) or 0),
            context_summary_tokens=int(d.get("context_summary_tokens", 0) or 0),
            mcp_plugins=(
                [str(x) for x in d["mcp_plugins"]]
                if "mcp_plugins" in d and d["mcp_plugins"] is not None
                else None
            ),
            builtin_toolsets=(
                [str(x) for x in d["builtin_toolsets"]]
                if "builtin_toolsets" in d and d["builtin_toolsets"] is not None
                else None
            ),
            uefn_plugins=(
                [str(x) for x in d["uefn_plugins"]]
                if "uefn_plugins" in d and d["uefn_plugins"] is not None
                else None
            ),
            token_usage=d.get("token_usage") if isinstance(d.get("token_usage"), dict) else None,
            prompt_cache_snapshot=(
                d.get("prompt_cache_snapshot") if isinstance(d.get("prompt_cache_snapshot"), dict) else None
            ),
            coding_agent=str(d.get("coding_agent", "ducky") or "ducky"),
            thinking_effort=str(d.get("thinking_effort", "") or "").strip().lower(),
            upstream_session_id=str(d.get("upstream_session_id", "") or ""),
            coding_agent_stats=(
                d.get("coding_agent_stats") if isinstance(d.get("coding_agent_stats"), dict) else None
            ),
            terminal_session_id=str(d.get("terminal_session_id", "") or ""),
            parent_conv_id=str(d.get("parent_conv_id", "") or ""),
            is_group=bool(d.get("is_group", False)),
            leader_conv_id=str(d.get("leader_conv_id", "") or "").strip(),
            group_members=(
                [dict(x) for x in d["group_members"] if isinstance(x, dict)]
                if isinstance(d.get("group_members"), list)
                else []
            ),
            tool_call_count=int(d.get("tool_call_count", 0) or 0),
            file_count=int(d.get("file_count", 0) or 0),
        )


def _default_folders() -> list[dict[str, Any]]:
    return []


def load_folders() -> list[ChatFolder]:
    path = folders_path()
    chats_root().mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        write_json_atomic(path, {"folders": _default_folders()})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("folders") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            return []
        folders = [ChatFolder.from_dict(f) for f in raw if isinstance(f, dict)]
        return folders
    except (json.JSONDecodeError, OSError):
        return []


def save_folders(folders: list[ChatFolder]) -> None:
    chats_root().mkdir(parents=True, exist_ok=True)
    write_json_atomic(folders_path(), {"folders": [f.to_dict() for f in folders]})


def create_folder(name: str, parent_id: str = "") -> ChatFolder:
    folders = load_folders()
    folder = ChatFolder(id=str(uuid.uuid4()), name=name.strip() or "Folder", parent_id=parent_id)
    folders.append(folder)
    save_folders(folders)
    return folder


def rename_folder(folder_id: str, name: str) -> None:
    folders = load_folders()
    for f in folders:
        if f.id == folder_id:
            f.name = name.strip() or f.name
            break
    save_folders(folders)


def delete_folder(folder_id: str) -> None:
    folders = [f for f in load_folders() if f.id != folder_id]
    save_folders(folders)
    for conv in list_conversations():
        if conv.folder_id == folder_id:
            conv.folder_id = ""
            save_conversation(conv)


def conversation_path(conv_id: str) -> Path:
    return conversations_dir() / f"{conv_id}.json"


def list_conversations(folder_id: str | None = None) -> list[Conversation]:
    """Deprecated global listing — delegates to project-scoped store."""
    from frontend.ui_web.project_chats import list_conversations as _list

    return _list(folder_id)


def load_conversation(conv_id: str) -> Conversation | None:
    """Deprecated global load — delegates to project-scoped store."""
    from frontend.ui_web.project_chats import load_conversation as _load

    return _load(conv_id)


def save_conversation(conv: Conversation) -> None:
    """Deprecated global save — delegates to project-scoped store."""
    from frontend.ui_web.project_chats import save_conversation as _save

    _save(conv)


def create_conversation(
    settings: PanelSettings | None = None,
    folder_id: str = "",
    *,
    skill_snapshot: str = "",
    enabled_packs: list[str] | None = None,
    enabled_subskills: dict[str, list[str]] | None = None,
    enabled_skills: list[str] | None = None,
    disabled_packs: list[str] | None = None,
    ducky_style: str = "classic",
    file_path: str = "",
) -> Conversation:
    del enabled_packs, enabled_subskills, enabled_skills
    now = time.time()
    s = settings or PanelSettings.load()
    from backend.skills.store import conversation_skill_text, merge_selection, skills_revision

    sel = merge_selection(disabled_packs=disabled_packs)
    snapshot = skill_snapshot or conversation_skill_text(
        disabled_packs=sel.disabled_packs,
        skill_snapshot="",
        mutate=False,
    )
    snapshot_rev = skills_revision()
    conv = Conversation(
        id=str(uuid.uuid4()),
        folder_id=folder_id or "",
        title="New ducky",
        created=now,
        updated=now,
        provider=s.agent_provider,
        model=s.agent_model,
        skill_snapshot=snapshot,
        skill_snapshot_rev=snapshot_rev,
        disabled_packs=list(sel.disabled_packs),
        enabled_packs=None,
        enabled_subskills=None,
        enabled_skills=None,
        ducky_style=normalize_ducky_style(ducky_style),
        file_path=norm_file_path(file_path),
    )
    save_conversation(conv)
    return conv


def bind_conversation_file_path(conv: Conversation, file_path: str) -> None:
    """Attach a file scope to a ducky when it is not already bound."""
    path = norm_file_path(file_path)
    if not path or conv.file_path:
        return
    conv.file_path = path
    save_conversation(conv)


def remap_conversation_file_path(path: str, from_prefix: str, to_prefix: str) -> str:
    """Rewrite a stored file_path after a project file move/rename."""
    norm = norm_file_path(path)
    src = norm_file_path(from_prefix).rstrip("/")
    dst = norm_file_path(to_prefix).rstrip("/")
    if not norm or not src:
        return norm
    if norm == src:
        return dst
    if norm.startswith(f"{src}/"):
        return f"{dst}/{norm[len(src) + 1:]}"
    return norm


def delete_conversation(conv_id: str) -> None:
    path = conversation_path(conv_id)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def auto_title(conv: Conversation, first_user_message: str) -> None:
    text = first_user_message.strip().replace("\n", " ")
    if not text:
        return
    conv.title = text[:60] + ("…" if len(text) > 60 else "")
    save_conversation(conv)


def append_message(conv: Conversation, message: dict[str, Any]) -> None:
    conv.messages.append(message)
    save_conversation(conv)


def strip_secrets_from_export(text: str) -> str:
    import re

    patterns = [
        r"sk-ant-[A-Za-z0-9_-]+",
        r"sk-[A-Za-z0-9_-]{20,}",
        r"AIza[A-Za-z0-9_-]{20,}",
    ]
    out = text
    for p in patterns:
        out = re.sub(p, "[REDACTED]", out)
    return out
