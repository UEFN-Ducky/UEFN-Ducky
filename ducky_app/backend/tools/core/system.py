"""Ping, workspace I/O, Python execution, logs, shutdown."""

from __future__ import annotations

import os
from typing import Any, Optional

from backend.bridge import resolve_workspace_path, send_command
from backend.util.json_util import tool_json
from backend.server import mcp
from backend.workspace.paths import (  # noqa: F401 - re-exported for existing importers
    WRITE_OUTSIDE_CONTENT_FORBIDDEN,
    WRITE_PYTHON_IN_PROJECT_FORBIDDEN,
    require_writable_project_path,
)


@mcp.tool()
def ping(pretty: bool = False) -> str:
    """Check if the UEFN editor listener is running (level/wiring only).

    NOT for Verse compile errors — use workspace_list_verse_errors (host, offline OK).
    Fail-fast when offline; do not retry waiting for the listener.
    """
    result = send_command("ping")
    return tool_json(result, pretty=pretty)


#: Internal listener commands agents must never call by name. These exist for
#: user-initiated recovery (undoing a run), not for agents to invoke.
_INTERNAL_COMMAND_PREFIX = "ducky_revert_"


@mcp.tool()
def listener_command(command: str, params: Optional[dict[str, Any]] = None, pretty: bool = False) -> str:
    """Run a listener command by name. Call ping for valid command names."""
    if (command or "").strip().startswith(_INTERNAL_COMMAND_PREFIX):
        raise ValueError(
            "Refused: never delete island content. That command is the undo path for a change the user chose to revert, not an agent tool. Fix the asset/device instead, or ask the user to revert the run from the Changes view."
        )
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


@mcp.tool()
def workspace_list_dir(relative_path: str = ".", path: str = "", pretty: bool = False) -> str:
    """List files under VS Code workspace folders on host disk.

    Skips Saved/, Intermediate/, DerivedDataCache/, and binary ``*.uasset`` /
    ``*.umap`` entries by default — grepping those freezes the machine.
    Prefer ``Content/Verse`` for Verse work.
    """
    # Agents habitually pass path= — honor it instead of silently listing ".".
    rel = (relative_path or "").strip()
    if (not rel or rel == ".") and (path or "").strip():
        rel = path.strip()
    rel = rel or "."
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
def workspace_read_file(relative_path: str = "", path: str = "", pretty: bool = False) -> str:
    """Read a text file from the VS Code workspace on host disk."""
    rel_in = (relative_path or "").strip() or (path or "").strip()
    if not rel_in:
        raise ValueError("relative_path is required (path= also accepted)")
    relative_path = rel_in
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
        from backend.workspace.runtime import get_writer

        get_writer().note_read(rel, text)  # base for concurrent-edit detection
    except Exception:
        pass
    try:
        from frontend.ui_web.verse_editor.agent_sync import emit_for_bridge_tool

        emit_for_bridge_tool("workspace_read_file", {"relative_path": rel}, {"data": payload})
    except Exception:
        pass
    return tool_json(payload, pretty=pretty)


@mcp.tool()
def workspace_write_file(relative_path: str, content: str, pretty: bool = False) -> str:
    """Write a text file under the VS Code workspace on host disk.

    Goes through the shared write pipeline: atomic replace, history snapshot with
    your identity, change journal, and write-lane policy when you are a laned
    group member (an out-of-lane path is refused — do not retry it).
    Only Content/** and .ducky/**. Never writes UEFN digests (*.digest.verse),
    .py/.pyc (Epic ContainsPythonData), or anything outside those two roots
    (Saved/, Intermediate/, project root). Python scratch → %LOCALAPPDATA%/UEFN-Ducky/.
    """
    from backend.workspace.runtime import get_writer

    result = get_writer().write_text(relative_path, content, tool="workspace_write_file")
    file_path = result.abs_path
    payload: dict[str, Any] = {
        "path": file_path,
        "relative_path": result.path,
        "bytes_written": result.bytes_written,
        "before_content": result.before_content,
        "lines_added": result.lines_added,
        "lines_removed": result.lines_removed,
    }
    extra = result.to_payload()
    for key in ("changeset", "in_lane", "warning"):
        if key in extra:
            payload[key] = extra[key]
    if result.path.lower().endswith(".verse"):
        # Findings only — never blocks the write. Catches the effect/using/API
        # mistakes the offline LSP scan cannot see.
        try:
            from backend.tools.verse.verse_lint import lint_verse, summarize_findings

            findings = lint_verse(content, file_path)
            payload["lint"] = summarize_findings(findings)
            if findings:
                payload["next"] = (
                    "Fix lint findings, then workspace_list_verse_errors; run "
                    "workspace_compile_verse before any wire_*/set_verse_editable/"
                    "set_npc_definition_behavior"
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

    In-memory only — never write any file to disk, especially not .py into the
    island (Epic ContainsPythonData). Never mutate *.digest.verse. Never loop
    spawn/import/prefab packaging — that freezes UEFN.
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
