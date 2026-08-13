"""Ping, workspace I/O, Python execution, logs, shutdown."""

from __future__ import annotations

import difflib
import os
from typing import Any, Optional

from backend.bridge import resolve_workspace_path, send_command, workspace_roots
from backend.util.json_util import tool_json
from backend.server import mcp


@mcp.tool()
def ping(pretty: bool = False) -> str:
    """Check if the UEFN editor listener is running (level/wiring only).

    NOT for Verse compile errors — use workspace_list_verse_errors (host, offline OK).
    Fail-fast when offline; do not retry waiting for the listener.
    """
    result = send_command("ping")
    return tool_json(result, pretty=pretty)


@mcp.tool()
def listener_command(command: str, params: Optional[dict[str, Any]] = None, pretty: bool = False) -> str:
    """Run a listener command by name. Call ping for valid command names."""
    result = send_command(command, params or {})
    return tool_json(result, pretty=pretty)


# Heavy / binary / derived UEFN dirs — listing or grepping these freezes the machine.
_WORKSPACE_SKIP_NAMES = frozenset(
    {
        "Saved",
        "Intermediate",
        "DerivedDataCache",
        "Binaries",
        ".git",
        "__pycache__",
        ".vs",
        ".idea",
    }
)
_WORKSPACE_SKIP_SUFFIXES = (".uasset", ".umap", ".dll", ".pdb", ".exe")


def _workspace_entry_allowed(name: str) -> bool:
    if name in _WORKSPACE_SKIP_NAMES:
        return False
    lower = name.lower()
    return not any(lower.endswith(suf) for suf in _WORKSPACE_SKIP_SUFFIXES)


WRITE_OUTSIDE_CONTENT_FORBIDDEN = (
    "workspace_write_file may only write under the UEFN project's Content/ or "
    ".ducky/ folders. Never touch UEFN core files, digests, Saved/, Intermediate/, "
    "or the project root. Scratch files belong in %LOCALAPPDATA%/UEFN-Ducky/ "
    "(or OS temp). Allowed: Content/** (Verse, assets) and .ducky/** (tests, tasks)."
)

_WRITE_BLOCKED_ANCESTORS = frozenset({"Saved", "Intermediate", "DerivedDataCache"})
_WRITE_ALLOWED_ANCESTORS = frozenset({"Content", ".ducky"})


def require_writable_project_path(file_path: str) -> None:
    """Refuse writes outside Content/** and .ducky/** of a UEFN project.

    Digests are blocked separately by require_not_digest_path. This guard stops
    the rest: Saved/, Intermediate/, project-root junk, and UEFN core files.
    """
    parts = os.path.normpath(os.path.abspath(file_path or "")).replace("\\", "/").split("/")
    names = [p for p in parts if p and p != "."]
    for i, name in enumerate(names):
        if name in _WRITE_BLOCKED_ANCESTORS:
            raise ValueError(WRITE_OUTSIDE_CONTENT_FORBIDDEN)
        if name in _WRITE_ALLOWED_ANCESTORS and i < len(names) - 1:
            return
    raise ValueError(WRITE_OUTSIDE_CONTENT_FORBIDDEN)


@mcp.tool()
def workspace_list_dir(relative_path: str = ".", pretty: bool = False) -> str:
    """List files under VS Code workspace folders on host disk.

    Skips Saved/, Intermediate/, DerivedDataCache/, and binary ``*.uasset`` /
    ``*.umap`` entries by default — grepping those freezes the machine.
    Prefer ``Content/Verse`` for Verse work.
    """
    rel = (relative_path or "").strip() or "."
    dir_path = resolve_workspace_path(rel)
    if not os.path.isdir(dir_path):
        raise ValueError(f"Not a directory: {dir_path}")
    names = sorted(n for n in os.listdir(dir_path) if _workspace_entry_allowed(n))
    hint = ""
    base = os.path.basename(dir_path.rstrip("\\/"))
    if base and base not in ("Verse", "Content") and "Verse" not in names:
        # Root-ish listing — steer agents away from binary Content/*.uasset greps.
        if any(x in names for x in ("Content", "Saved", "Intermediate")) or not names:
            hint = "Prefer Content/Verse for Verse searches — never grep *.uasset or Saved/."
    payload: dict[str, Any] = {"path": dir_path, "entries": names}
    if hint:
        payload["hint"] = hint
    return tool_json(payload, pretty=pretty)


@mcp.tool()
def workspace_read_file(relative_path: str, pretty: bool = False) -> str:
    """Read a text file from the VS Code workspace on host disk."""
    file_path = resolve_workspace_path(relative_path)
    if not os.path.isfile(file_path):
        # A weak model that guesses a wrong path otherwise re-flails on the same
        # dead read for turns. Hand it the real directory contents so it can
        # self-correct in one step instead of looping.
        parent = os.path.dirname(file_path)
        hint = "Do not retry this path — use workspace_list_dir to find the real name."
        if os.path.isdir(parent):
            try:
                names = sorted(os.listdir(parent))
                shown = names[:25]
                more = "" if len(names) <= 25 else f" (+{len(names) - 25} more)"
                hint = (
                    f"That path does not exist. Its parent folder contains: {shown}{more}. "
                    "Pick a real name from this list or use workspace_list_dir; do not retry the same path."
                )
            except OSError:
                pass
        raise ValueError(f"Not a file: {file_path}. {hint}")
    with open(file_path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    rel = relative_path.strip().replace("\\", "/")
    payload = {"path": file_path, "content": text}
    try:
        from frontend.ui_web.verse_editor.agent_sync import emit_for_bridge_tool

        emit_for_bridge_tool("workspace_read_file", {"relative_path": rel}, {"data": payload})
    except Exception:
        pass
    return tool_json(payload, pretty=pretty)


@mcp.tool()
def workspace_write_file(relative_path: str, content: str, pretty: bool = False) -> str:
    """Write a text file under the VS Code workspace on host disk.

    Only Content/** and .ducky/**. Never writes UEFN digests (*.digest.verse)
    or anything outside those two roots (Saved/, Intermediate/, project root).
    """
    from backend.tools.verse.verse_digests import require_not_digest_path

    require_not_digest_path(relative_path)
    file_path = resolve_workspace_path(relative_path)
    require_not_digest_path(file_path)
    require_writable_project_path(file_path)
    parent = os.path.dirname(file_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    before_content = ""
    if os.path.isfile(file_path):
        with open(file_path, encoding="utf-8", errors="replace") as f:
            before_content = f.read()
    with open(file_path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    rel = relative_path.strip().replace("\\", "/")
    try:
        from frontend.ui_web.verse_editor import file_history

        roots = workspace_roots()
        file_history.record_agent_write(
            rel,
            before_content,
            content,
            project_root=roots[0] if roots else None,
        )
    except Exception:
        pass
    before_lines = before_content.splitlines()
    after_lines = content.splitlines()
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines)
    lines_added = 0
    lines_removed = 0
    for tag, _i1, _i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "insert":
            lines_added += _j2 - _j1
        elif tag == "delete":
            lines_removed += _i2 - _i1
        elif tag == "replace":
            lines_removed += _i2 - _i1
            lines_added += _j2 - _j1
    payload = {
        "path": file_path,
        "relative_path": rel,
        "bytes_written": len(content.encode("utf-8")),
        "before_content": before_content,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
    }
    try:
        from frontend.ui_web.verse_editor.agent_sync import emit_for_bridge_tool

        emit_for_bridge_tool(
            "workspace_write_file",
            {"relative_path": rel, "content": content},
            {"data": payload},
        )
    except Exception:
        pass
    return tool_json(payload, pretty=pretty)


@mcp.tool()
def reload_listener(pretty: bool = False) -> str:
    """Re-sync listener code from UEFN-Ducky and restart in-editor HTTP server without restarting UEFN.

    Run after updating UEFN-Ducky.exe (start the bridge first so the bundle HTTP server is up).
    """
    result = send_command("reload_listener", timeout=120.0)
    return tool_json(result, pretty=pretty)


@mcp.tool()
def execute_python(code: str) -> str:
    """Run arbitrary Python in UEFN (escape hatch). Call uefn_editor_python_hints first for materials/Fort*.

    Never write files outside the project's Content/** or .ducky/**. Never mutate
    *.digest.verse. Never loop spawn/import/prefab packaging — that freezes UEFN.
    """
    result = send_command("execute_python", {"code": code}, timeout=120.0)
    parts = []
    if result.get("stdout"):
        parts.append(f"stdout:\n{result['stdout']}")
    if result.get("stderr"):
        parts.append(f"stderr:\n{result['stderr']}")
    if result.get("result") is not None:
        parts.append(f"result: {tool_json(result['result'])}")
    return "\n".join(parts) if parts else "(no output)"


@mcp.tool()
def get_log(last_n: int = 50) -> str:
    """Get recent MCP listener log entries."""
    result = send_command("get_log", {"last_n": last_n})
    return "\n".join(result.get("lines", []))


@mcp.tool()
def get_editor_log(
    last_n: int = 100,
    filter_str: str = "",
    since_offset: int = 0,
    regex: str = "",
) -> str:
    """Read recent Unreal Editor Output Log lines.

    ``since_offset`` streams new bytes after a prior response's offset; ``regex``
    filters lines (preferred over ``filter_str`` when set).
    """
    result = send_command(
        "get_editor_log",
        {
            "last_n": last_n,
            "filter_str": filter_str,
            "since_offset": since_offset,
            "regex": regex,
        },
    )
    lines = result.get("lines", [])
    if result.get("error"):
        return f"Error: {result['error']}"
    return "\n".join(lines)


@mcp.tool()
def shutdown(pretty: bool = False) -> str:
    """Gracefully stop the UEFN listener."""
    result = send_command("shutdown", timeout=5.0)
    return tool_json(result, pretty=pretty)
