"""Scan agent conversations for files edited during the session."""

from __future__ import annotations

from typing import Any

_WRITE_TOOLS = frozenset({"workspace_write_file", "create_project_verse_file", "create_project_file"})
_PATH_TOOLS = frozenset({"rename_project_entry", "move_project_entry"})


def _norm_path(path: str) -> str:
    return (path or "").strip().replace("\\", "/")


def _path_from_args(args: dict[str, Any]) -> str:
    for key in ("relative_path", "path", "source_relative", "dest_parent_relative"):
        raw = args.get(key)
        if isinstance(raw, str) and raw.strip():
            return _norm_path(raw)
    return ""


def _lines_in_content(content: str) -> int:
    if not content:
        return 0
    return content.count("\n") + (1 if content and not content.endswith("\n") else 0)


def _lines_from_write_args(args: dict[str, Any]) -> int:
    content = args.get("content")
    if isinstance(content, str):
        return _lines_in_content(content)
    return 0


def session_stats_from_messages(messages: list[Any]) -> dict[str, int]:
    """Count tool calls and unique session files from conversation messages."""
    tool_call_count = 0
    file_paths: set[str] = set()

    for message in messages:
        if not isinstance(message, dict):
            continue
        for block in message.get("blocks") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_call":
                continue
            tool_call_count += 1
            if message.get("role") != "assistant":
                continue
            if str(block.get("status") or "") != "success":
                continue
            name = str(block.get("name") or "")
            args = block.get("arguments") if isinstance(block.get("arguments"), dict) else {}
            if name in _WRITE_TOOLS or name in _PATH_TOOLS:
                path = _path_from_args(args)
                if path:
                    file_paths.add(path)

    return {"tool_call_count": tool_call_count, "file_count": len(file_paths)}


def compute_session_files(conv_id: str) -> list[dict[str, Any]]:
    """Return deduplicated session file entries from successful write/path tools."""
    from frontend.ui_web.project_chats import load_conversation

    conv = load_conversation(conv_id)
    if not conv:
        return []

    by_path: dict[str, dict[str, Any]] = {}

    for message in conv.messages:
        if message.get("role") != "assistant":
            continue
        for block in message.get("blocks") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_call":
                continue
            name = str(block.get("name") or "")
            status = str(block.get("status") or "")
            if status != "success":
                continue
            args = block.get("arguments") if isinstance(block.get("arguments"), dict) else {}

            if name in _WRITE_TOOLS:
                path = _path_from_args(args)
                if not path:
                    continue
                kind = "create" if name in ("create_project_verse_file", "create_project_file") else "write"
                entry: dict[str, Any] = {"path": path, "kind": kind}
                lines = _lines_from_write_args(args)
                if lines > 0:
                    entry["lines_added"] = lines
                by_path[path] = entry
            elif name in _PATH_TOOLS:
                path = _path_from_args(args)
                if not path:
                    continue
                kind = "rename" if name == "rename_project_entry" else "move"
                by_path[path] = {"path": path, "kind": kind}

    return list(by_path.values())
