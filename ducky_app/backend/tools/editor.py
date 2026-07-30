"""Editor control tools: console, PIE, screenshots, saving, properties."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from backend.bridge import send_command
from backend.json_util import tool_json
from backend.tools.plugin_gate import plugin_mcp_tool

# Listener returns quickly (viewport buffer capture). PNG may land next frame —
# wait here on the host, never inside the UE tick.
_SCREENSHOT_FILE_WAIT_SEC = 8.0
_SCREENSHOT_BRIDGE_TIMEOUT_SEC = 45.0


def _wait_for_screenshot_file(result: dict[str, Any]) -> dict[str, Any]:
    """Poll for the PNG on the host after a non-blocking viewport capture."""
    if not isinstance(result, dict):
        return result
    path = str(result.get("path") or "").strip()
    if not path:
        return result
    src = Path(path)
    if src.is_file() and src.stat().st_size > 0:
        out = {**result}
        out.pop("await_path", None)
        return out
    deadline = time.time() + _SCREENSHOT_FILE_WAIT_SEC
    while time.time() < deadline:
        if src.is_file() and src.stat().st_size > 0:
            out = {**result}
            out.pop("await_path", None)
            out.pop("hint", None)
            return out
        time.sleep(0.05)
    return {
        **result,
        "error": (
            f"Screenshot file not ready after {_SCREENSHOT_FILE_WAIT_SEC:.0f}s: {path}. "
            "Retry once; if UEFN was frozen, reload the listener (viewport capture path)."
        ),
    }


def _enrich_screenshot(result: dict[str, Any]) -> dict[str, Any]:
    """Attach media_url + project DuckyCaptures copy; keep base64 out of results.

    Agents must use the project ``path`` for file work (import, copy, py). AppData
    ``capture_path`` / ``media_url`` are for chat UI preview only — agents do not
    have reliable access to AppData folders.
    """
    if not isinstance(result, dict):
        return result
    if result.get("error") and not Path(str(result.get("path") or "")).is_file():
        return result
    path = (result.get("path") or "").strip()
    if not path:
        return result
    src = Path(path)
    if not src.is_file():
        return result
    try:
        from frontend.ui_web.tool_captures import save_capture_for_agents

        raw = src.read_bytes()
        saved = save_capture_for_agents(raw, prefix="uefn_viewport")
        out = {**result}
        # Prefer DuckyCaptures when project root is set; else keep UE Screenshots path.
        project_path = str(saved.get("path") or "")
        if project_path and Path(project_path).is_file():
            out["path"] = project_path
            out["ue_screenshot_path"] = path
        else:
            out["path"] = path
        out["capture_path"] = str(saved.get("capture_path") or saved.get("path") or "")
        out["media_url"] = saved.get("media_url") or ""
        out["capture_filename"] = saved.get("filename") or ""
        out["bytes"] = saved.get("bytes")
        out["hint"] = (
            "Use project path for file work; media_url/capture_path are AppData "
            "preview-only. Image is also returned as MCP image content when available."
        )
        out.pop("await_path", None)
        return out
    except Exception as exc:
        return {**result, "capture_error": str(exc)[:200]}


def _screenshot_mcp_payload(result: dict[str, Any], *, pretty: bool) -> Any:
    """Text JSON for tools + FastMCP Image so Cursor/IDE agents can see the PNG."""
    text = tool_json(result, pretty=pretty)
    path = str(result.get("path") or "").strip()
    if not path or not Path(path).is_file():
        return text
    try:
        from mcp.server.fastmcp import Image

        return [text, Image(path=path)]
    except Exception:
        return text


@plugin_mcp_tool("uefn")
def exec_console_command(command: str, pretty: bool = False) -> str:
    """Run an editor console command (e.g. 'stat fps')."""
    return tool_json(send_command("exec_console_command", {"command": command}), pretty=pretty)


@plugin_mcp_tool("uefn")
def save_all_dirty(content: bool = True, maps: bool = True, pretty: bool = False) -> str:
    """Save all dirty content packages and/or maps without prompting."""
    return tool_json(send_command("save_all_dirty", {"content": content, "maps": maps}), pretty=pretty)


@plugin_mcp_tool("uefn")
def take_high_res_screenshot(
    width: int = 1280, height: int = 720, filename: str = "", pretty: bool = False
) -> Any:
    """Capture the active viewport for visual verify (does not freeze UEFN).

    Uses a fast viewport-buffer capture on the listener (not Unreal's high-res
    offscreen path, which stalls the editor on dense levels). Returns project
    ``path`` (under ``Saved/DuckyCaptures`` when project root is set, else
    ``Saved/Screenshots``) + ``media_url`` for panel preview, and an MCP image
    content block so vision-capable agents can see the capture.
    Use ``path`` for any file work; ``media_url`` / ``capture_path`` are
    AppData preview-only. Never Bash-find for screenshot PNGs.
    ``width``/``height`` are advisory; the PNG matches the current viewport.
    """
    result = send_command(
        "take_high_res_screenshot",
        {"width": width, "height": height, "filename": filename},
        timeout=_SCREENSHOT_BRIDGE_TIMEOUT_SEC,
    )
    if isinstance(result, dict):
        if result.get("error") and not result.get("path"):
            return tool_json(result, pretty=pretty)
        result = _wait_for_screenshot_file(result)
        if result.get("error") and not Path(str(result.get("path") or "")).is_file():
            return tool_json(result, pretty=pretty)
        result = _enrich_screenshot(result)
        return _screenshot_mcp_payload(result, pretty=pretty)
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def play_in_editor(pretty: bool = False) -> str:
    """Start Play-In-Editor (best effort; may be restricted in some UEFN builds)."""
    return tool_json(send_command("play_in_editor", {}), pretty=pretty)


@plugin_mcp_tool("uefn")
def stop_pie(pretty: bool = False) -> str:
    """Stop Play-In-Editor / simulation (best effort)."""
    return tool_json(send_command("stop_pie", {}), pretty=pretty)


@plugin_mcp_tool("uefn")
def set_object_property(
    asset_path: str, property_name: str, value: Any, save: bool = True, pretty: bool = False
) -> str:
    """Set an editor property on a loaded asset object, then optionally save it."""
    return tool_json(
        send_command(
            "set_object_property",
            {
                "asset_path": asset_path,
                "property_name": property_name,
                "value": value,
                "save": save,
            },
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("uefn")
def get_editor_stats(pretty: bool = False) -> str:
    """Lightweight editor/world summary (world name, actor count, engine version)."""
    return tool_json(send_command("get_editor_stats", {}), pretty=pretty)
