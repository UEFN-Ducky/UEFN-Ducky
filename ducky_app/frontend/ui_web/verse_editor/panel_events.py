"""Push verse editor events to all panel webviews."""

from __future__ import annotations

from typing import Any


def push_agent_event(event: dict[str, Any]) -> None:
    from frontend.ui_web.agent_modes import _resolve_push

    _resolve_push(None)(event)


def _has_local_panel() -> bool:
    """Only stream diagnostics into an in-process panel.

    Coding-agent MCP servers run in separate bridge processes. Forwarding every
    per-file diagnostics event from those processes can wedge WebView2 while the
    agent already receives the authoritative scan result directly.
    """
    from frontend.ui_web.agent_modes import get_panel_push

    return get_panel_push() is not None


def push_verse_diagnostics_sync(
    files: list[dict[str, Any]],
    project_root: str = "",
    *,
    replace: bool = False,
    live: bool = False,
) -> None:
    if not _has_local_panel():
        return
    payload: dict[str, Any] = {"type": "verse_diagnostics_sync", "files": files, "replace": replace}
    if project_root:
        payload["project_root"] = project_root
    if live:
        # From live verse-lsp (open/save/recheck) — clean results may clear Problems.
        payload["live"] = True
    push_agent_event(payload)


def push_verse_diagnostics_progress(payload: dict[str, Any]) -> None:
    if not _has_local_panel():
        return
    push_agent_event({"type": "verse_diagnostics_progress", **payload})


def push_verse_workflow_state(state: dict[str, Any]) -> None:
    push_agent_event({"type": "verse_workflow_state", **state})
