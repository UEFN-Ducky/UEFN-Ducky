"""Build editor_batch events from agent workspace file tool results."""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
from typing import Any, Callable

from frontend.ui_web.verse_editor import io
from frontend.ui_web.verse_editor.feature_flag import verse_editor_enabled
from frontend.ui_web.verse_editor.types import EditorAction, EditorBatch, EditorRange

PushFn = Callable[[dict[str, Any]], None]

_READ_TOOL = "workspace_read_file"
_WRITE_TOOL = "workspace_write_file"
_WRITE_TOOLS = frozenset(
    {
        _WRITE_TOOL,
        "create_project_verse_file",
        "create_project_file",
        "Write",
        "Edit",
        "StrReplace",
    }
)
_CREATE_TOOLS = frozenset({"create_project_verse_file", "create_project_file", "Write"})
# Cursor / Claude coding-agent aliases (normalized via normalize_workspace_tool_name).
_TOOL_ALIASES = {
    "write": "Write",
    "edit": "Edit",
    "strreplace": "StrReplace",
    "search_replace": "StrReplace",
    "searchreplace": "StrReplace",
    "applypatch": "ApplyPatch",
    "apply_patch": "ApplyPatch",
    "read": "Read",
}


def _norm_path(path: str) -> str:
    return (path or "").strip().replace("\\", "/")


def _coerce_project_path(path: str) -> str:
    """Turn Cursor absolute paths into project-relative (or abs: encoded) keys."""
    raw = (path or "").strip()
    if not raw:
        return ""
    norm = _norm_path(raw)
    # Already project-relative or encoded.
    if not (len(norm) >= 2 and norm[1] == ":") and not norm.startswith("//"):
        return norm.lstrip("/")
    try:
        from frontend.ui_web import project_files as pf

        abs_p = Path(raw).resolve()
        root = pf._project_root().resolve()  # noqa: SLF001
        if os.path.commonpath([str(abs_p), str(root)]) == str(root):
            return str(abs_p.relative_to(root)).replace("\\", "/")
        return pf._encode_abs_path(abs_p)  # noqa: SLF001
    except Exception:
        return norm


def _arg_str(args: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        val = args.get(key)
        if isinstance(val, str):
            return val
    return None


def _line_diff_actions(path: str, old_text: str, new_text: str) -> list[EditorAction]:
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    actions: list[EditorAction] = []
    max_hunks = 12
    hunks = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if hunks >= max_hunks:
            break
        hunks += 1
        start_line = i1 + 1
        actions.append(
            EditorAction(
                type="scroll_to",
                path=path,
                range=EditorRange(start_line, 1, start_line, 1),
                duration_ms=200,
            )
        )
        if tag in ("replace", "delete"):
            actions.append(
                EditorAction(
                    type="highlight",
                    path=path,
                    range=EditorRange(i1 + 1, 1, max(i2, i1 + 1), 1),
                    style="selection",
                    duration_ms=400,
                )
            )
            actions.append(
                EditorAction(
                    type="delete_preview",
                    path=path,
                    range=EditorRange(i1 + 1, 1, max(i2, i1 + 1), 1),
                    style="delete",
                    duration_ms=500,
                )
            )
        if tag in ("replace", "insert"):
            insert_text = "".join(new_lines[j1:j2])
            actions.append(
                EditorAction(
                    type="insert_preview",
                    path=path,
                    range=EditorRange(j1 + 1, 1, j1 + 1, 1),
                    text=insert_text,
                    style="insert",
                    duration_ms=500,
                )
            )

    actions.append(EditorAction(type="apply_content", path=path, text=new_text))
    actions.append(EditorAction(type="clear_decorations", path=path))
    return actions


def _parse_tool_result_data(raw_result: dict[str, Any]) -> dict[str, Any]:
    data = raw_result.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, str) and data.strip().startswith("{"):
        try:
            parsed = json.loads(data)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def normalize_workspace_tool_name(tool_name: str) -> str:
    """Strip MCP prefixes (mcp__uefn__workspace_write_file → workspace_write_file)."""
    name = (tool_name or "").strip()
    knowns = (
        _WRITE_TOOL,
        _READ_TOOL,
        "create_project_verse_file",
        "create_project_file",
        "Write",
        "Edit",
        "StrReplace",
        "Read",
        "ApplyPatch",
    )
    for known in knowns:
        if name == known or name.endswith(f"__{known}") or name.endswith(f"/{known}"):
            return known
    if "__" in name:
        name = name.rsplit("__", 1)[-1]
    alias = _TOOL_ALIASES.get(name.lower())
    if alias:
        return alias
    return name


def _before_from_history(path: str) -> str:
    """Pre-write snapshot from file_history (coding-agent bridge already recorded it)."""
    try:
        from frontend.ui_web.verse_editor import file_history

        for entry in file_history.list_entries(path):
            if entry.get("source") == "agent":
                continue
            return file_history.read_entry(path, str(entry["id"]))["content"]
        entries = file_history.list_entries(path)
        if len(entries) >= 2:
            return file_history.read_entry(path, str(entries[1]["id"]))["content"]
    except Exception:
        pass
    return ""


def seed_before_edit(tool_name: str, args: dict[str, Any] | None) -> None:
    """Snapshot file into content cache when an edit tool starts (before disk changes)."""
    tool_name = normalize_workspace_tool_name(tool_name)
    if tool_name not in _WRITE_TOOLS:
        return
    path = _coerce_project_path(
        str(
            (args or {}).get("relative_path")
            or (args or {}).get("path")
            or (args or {}).get("file_path")
            or ""
        )
    )
    if not path:
        return
    try:
        if io.get_cached(path) is not None:
            return
        io.read_file(path)
    except Exception:
        pass


def build_file_edit_meta(tool_name: str, args: dict[str, Any], raw_result: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Build UI file-edit metadata for workspace write + Cursor Edit/Write tools."""
    tool_name = normalize_workspace_tool_name(tool_name)
    if tool_name not in _WRITE_TOOLS:
        return None
    raw_result = raw_result or {}
    path_guess = _coerce_project_path(
        str(args.get("relative_path") or args.get("path") or args.get("file_path") or "")
    )
    # Capture cache baseline BEFORE any read_file (which overwrites the cache).
    cached_before = io.get_cached(path_guess) if path_guess else None
    path, new_content, before_hint = _parse_write_content(args, raw_result, tool_name)
    if not path:
        result_data = _parse_tool_result_data(raw_result)
        path = _coerce_project_path(str(result_data.get("relative_path") or result_data.get("path") or ""))
    if not path:
        return None
    if cached_before is None:
        cached_before = io.get_cached(path)
    if not new_content:
        try:
            new_content = io.read_file(path)["content"]
        except ValueError:
            pass
    result_data = _parse_tool_result_data(raw_result)
    before = str(result_data.get("before_content") or "") or (before_hint or "")
    if not before and cached_before is not None:
        before = cached_before
    if not before:
        before = _before_from_history(path)
    lines_added = int(result_data.get("lines_added") or 0)
    lines_removed = int(result_data.get("lines_removed") or 0)
    if lines_added == 0 and lines_removed == 0 and before != new_content:
        before_lines = before.splitlines()
        after_lines = new_content.splitlines()
        matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "insert":
                lines_added += j2 - j1
            elif tag == "delete":
                lines_removed += i2 - i1
            elif tag == "replace":
                lines_removed += i2 - i1
                lines_added += j2 - j1
    kind = "create" if tool_name in _CREATE_TOOLS and not before else "write"
    return {
        "path": path,
        "before": before,
        "after": new_content,
        "linesAdded": lines_added,
        "linesRemoved": lines_removed,
        "kind": kind,
    }


def file_edit_meta_for_stream(tool_name: str, args: dict[str, Any], result_text: str) -> dict[str, Any] | None:
    """Rebuild fileEdit for coding-agent tool_done payloads (Cursor/Claude/Codex)."""
    raw: dict[str, Any] = {"data": result_text if isinstance(result_text, str) else ""}
    return build_file_edit_meta(tool_name, args if isinstance(args, dict) else {}, raw)


def _parse_write_content(
    args: dict[str, Any],
    raw_result: dict[str, Any],
    tool_name: str = "",
) -> tuple[str, str, str | None]:
    """Return (path, after_content, before_hint). before_hint is set for search-replace tools."""
    rel = _coerce_project_path(
        str(args.get("relative_path") or args.get("path") or args.get("file_path") or "")
    )
    for key in ("content", "contents", "new_content", "text"):
        content = args.get(key)
        if isinstance(content, str):
            return rel, content, None

    old = _arg_str(args, "old_string", "oldString", "old_str", "oldText", "old_text")
    new = _arg_str(args, "new_string", "newString", "new_str", "newText", "new_text")
    if rel and old is not None and new is not None:
        cached = io.get_cached(rel)
        # Prefer reverse-apply from disk (edit already landed).
        try:
            after = io.read_file(rel)["content"]
        except ValueError:
            after = ""
        if after and new and new in after:
            before = after.replace(new, old, 1)
            return rel, after, before
        before = cached if cached is not None else _before_from_history(rel)
        if not before:
            before = after
        if old in before:
            return rel, before.replace(old, new, 1), before
        return rel, after or before, before or None

    data = raw_result.get("data")
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            if isinstance(parsed, dict) and "content" in parsed:
                return (
                    rel or _coerce_project_path(str(parsed.get("path", ""))),
                    str(parsed["content"]),
                    None,
                )
        except json.JSONDecodeError:
            pass
    # Path-only Cursor Edit — after from disk; before from cache/history in build_file_edit_meta.
    if rel and tool_name in ("Edit", "StrReplace", "Write", _WRITE_TOOL):
        return rel, "", None
    return rel, "", None


def build_open_verse_file_batch(path: str, line: int = 1, column: int = 1) -> EditorBatch | None:
    path = _norm_path(path)
    if not path or not path.lower().endswith(".verse"):
        return None
    return EditorBatch(
        actions=[
            EditorAction(type="open_file", path=path, activate=False),
            EditorAction(
                type="scroll_to",
                path=path,
                range=EditorRange(line, column, line, column),
                duration_ms=200,
            ),
            EditorAction(
                type="highlight",
                path=path,
                range=EditorRange(line, column, line, column),
                style="agent_cursor",
                duration_ms=600,
            ),
            EditorAction(type="clear_decorations", path=path),
        ],
    )


def build_editor_batch(conv_id: str, tool_name: str, args: dict[str, Any], raw_result: dict[str, Any]) -> EditorBatch | None:
    if not verse_editor_enabled():
        return None
    tool_name = normalize_workspace_tool_name(tool_name)
    # Reads must never open editor tabs — only warm the content cache for later diffs.
    if tool_name in (_READ_TOOL, "Read"):
        path = _coerce_project_path(
            str(args.get("relative_path") or args.get("path") or args.get("file_path") or "")
        )
        if path and path.lower().endswith(".verse"):
            try:
                io.read_file(path)
            except ValueError:
                pass
        return None

    if tool_name in _WRITE_TOOLS:
        path, new_content, before_hint = _parse_write_content(args, raw_result, tool_name)
        if not path or not path.lower().endswith(".verse"):
            return None
        result_data = _parse_tool_result_data(raw_result)
        before_from_result = result_data.get("before_content")
        cached_before = io.get_cached(path)
        if isinstance(before_from_result, str):
            old_content = before_from_result
        elif before_hint is not None:
            old_content = before_hint
        elif cached_before is not None:
            old_content = cached_before
        else:
            try:
                # Last resort — may already be post-edit; seed_before_edit should have cached.
                old_content = io.read_file(path)["content"] if not new_content else ""
            except ValueError:
                old_content = ""
        if not new_content:
            try:
                new_content = io.read_file(path)["content"]
            except ValueError:
                new_content = ""
        io.seed_cache(path, new_content)
        if old_content == new_content:
            actions = [EditorAction(type="apply_content", path=path, text=new_content)]
        else:
            # Open beside chat so Follow Code can walk through the diff (settings copy).
            actions = [
                EditorAction(type="open_file", path=path, activate=False),
                *_line_diff_actions(path, old_content, new_content),
            ]
        return EditorBatch(actions=actions, conv_id=conv_id)

    return None


def _push_batch(push: PushFn, conv_id: str, batch: EditorBatch) -> None:
    push(
        {
            "type": "editor_batch",
            "conv_id": conv_id,
            "editor_batch": batch.to_dict(),
        }
    )
    sync_path = next((a.path for a in batch.actions if a.path), "")
    if sync_path:
        push({"type": "file_sync", "conv_id": conv_id, "path": sync_path})


def emit_editor_events(push: PushFn, conv_id: str, rec: Any) -> None:
    """Called from agent_modes after tool_done — must never raise."""
    try:
        name = getattr(rec, "name", "") or ""
        args = dict(getattr(rec, "arguments", None) or {})
        raw_result = getattr(rec, "result", None) or {}
        if not isinstance(raw_result, dict):
            raw_result = {}
        batch = build_editor_batch(conv_id, name, args, raw_result)
        if not batch or not batch.actions:
            return
        _push_batch(push, conv_id, batch)
    except Exception:
        pass


def emit_for_bridge_tool(
    tool_name: str,
    args: dict[str, Any],
    raw_result: dict[str, Any],
    *,
    conv_id: str = "",
) -> None:
    """Push editor_batch from MCP bridge tools. No-op in the panel process (avoids double-emit)."""
    try:
        from frontend.ui_web.agent_modes import get_panel_push

        if get_panel_push() is not None:
            return
        from frontend.ui_web.verse_editor.panel_events import push_agent_event

        batch = build_editor_batch(conv_id, tool_name, args, raw_result)
        if not batch or not batch.actions:
            return
        _push_batch(push_agent_event, conv_id, batch)
    except Exception:
        pass
