"""Editor control tools: console, PIE, screenshots, saving, properties."""

from __future__ import annotations

import base64
import tempfile
import time
from pathlib import Path
from typing import Any

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool

# Listener returns quickly (viewport buffer capture). PNG may land next frame —
# wait here on the host, never inside the UE tick.
_SCREENSHOT_FILE_WAIT_SEC = 8.0
_SCREENSHOT_BRIDGE_TIMEOUT_SEC = 45.0


def _drop_temp_capture(path: str) -> None:
    """Remove a listener leftover under %TEMP%/ducky_captures. Leave every other file."""
    src = Path(path)
    try:
        src.resolve().relative_to((Path(tempfile.gettempdir()) / "ducky_captures").resolve())
    except (ValueError, OSError):
        return
    try:
        src.unlink()
    except OSError:
        pass


def _png_bytes(result: dict[str, Any]) -> bytes:
    path = str(result.get("path") or "").strip()
    raw = b""
    b64 = str(result.get("png_base64") or "").strip()
    if b64:
        try:
            raw = base64.b64decode(b64)
        except Exception:
            raw = b""
    elif path and Path(path).is_file():
        try:
            raw = Path(path).read_bytes()
        except OSError:
            raw = b""
    if path:
        _drop_temp_capture(path)
    return raw


def _wait_for_screenshot_file(result: dict[str, Any]) -> dict[str, Any]:
    """Poll for the PNG on the host after a non-blocking viewport capture."""
    if not isinstance(result, dict):
        return result
    if str(result.get("png_base64") or "").strip():
        return result
    path = str(result.get("path") or "").strip()
    if not path:
        # No path at all means the capture never produced a file. Saying
        # "kicked off, wait a frame" here is how a dead capture looked like a
        # pending one — with nothing on its way.
        return {
            **result,
            "error": (
                "Screenshot failed: the listener returned no path, so no PNG was written. "
                "Reload the listener and retry; if it persists, viewport capture is "
                "unavailable in this UEFN build."
            ),
        }
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
    """Save the PNG in the active chat folder and keep base64 out of the result.

    The only file is ``chats/projects/…/conversations/<chat>/attachments``.
    """
    if not isinstance(result, dict):
        return result
    raw = _png_bytes(result)
    out = {k: v for k, v in result.items() if k != "png_base64"}
    if result.get("error") and not raw:
        return out
    if not raw:
        return out
    try:
        from frontend.ui_web.tool_captures import save_capture_for_agents

        saved = save_capture_for_agents(raw, prefix="uefn_viewport")
        if not saved.get("ok") or not Path(str(saved.get("path") or "")).is_file():
            out["error"] = str(saved.get("error") or "Screenshot was not saved: no active chat.")
            out.pop("path", None)
            return out
        out["path"] = str(saved.get("path") or "")
        out["capture_path"] = str(saved.get("capture_path") or saved.get("path") or "")
        out["media_url"] = saved.get("media_url") or ""
        out["capture_filename"] = saved.get("filename") or ""
        out["conv_id"] = saved.get("conv_id") or ""
        out["bytes"] = saved.get("bytes")
        out["hint"] = (
            "Capture is in this chat's AppData attachments folder. "
            "Image is also returned as MCP image content when available."
        )
        out.pop("await_path", None)
        out.pop("error", None)
        return out
    except Exception as exc:
        return {**out, "capture_error": str(exc)[:200]}


def _screenshot_mcp_payload(result: dict[str, Any], *, pretty: bool) -> Any:
    """Text JSON for tools + FastMCP Image so Cursor/IDE agents can see the PNG."""
    text = tool_json(result, pretty=pretty)
    path = str(result.get("path") or "").strip()
    if not path or not Path(path).is_file():
        return text
    try:
        from backend.tools.core.model_picture import model_picture

        return [text, model_picture(path)]
    except Exception:
        return text


@plugin_mcp_tool("uefn")
def exec_console_command(command: str, pretty: bool = False) -> str:
    """Run one editor console command (e.g. 'stat fps'). Not for Python (execute_python)."""
    return tool_json(send_command("exec_console_command", {"command": command}), pretty=pretty)


@plugin_mcp_tool("uefn")
def save_all_dirty(content: bool = True, maps: bool = True, pretty: bool = False) -> str:
    """Save all dirty content packages and/or maps without prompting. Not behind an open Save popup."""
    return tool_json(send_command("save_all_dirty", {"content": content, "maps": maps}), pretty=pretty)


@plugin_mcp_tool("uefn")
def take_high_res_screenshot(
    width: int = 1280, height: int = 720, filename: str = "", pretty: bool = False
) -> Any:
    """Capture the active viewport into this chat to check a layout. Does not freeze UEFN.

    Uses a fast viewport-buffer capture on the listener (not Unreal's high-res
    offscreen path, which stalls the editor on dense levels). Returns ``path``
    under this chat's AppData attachments folder (never Temp, tool_captures, or
    the UEFN project) + ``media_url`` for the chat card, and an MCP image block.
    Never Bash-find for screenshot PNGs. ``width``/``height`` are advisory;
    the PNG matches the current viewport.
    """
    result = send_command(
        "take_high_res_screenshot",
        {"width": width, "height": height, "filename": filename},
        timeout=_SCREENSHOT_BRIDGE_TIMEOUT_SEC,
    )
    if isinstance(result, dict):
        if result.get("error") and not result.get("path") and not result.get("png_base64"):
            return tool_json(result, pretty=pretty)
        result = _wait_for_screenshot_file(result)
        has_bytes = bool(str(result.get("png_base64") or "").strip()) or Path(
            str(result.get("path") or "")
        ).is_file()
        if result.get("error") and not has_bytes:
            return tool_json(result, pretty=pretty)
        result = _enrich_screenshot(result)
        return _screenshot_mcp_payload(result, pretty=pretty)
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def set_object_property(
    asset_path: str, property_name: str, value: Any, save: bool = True, pretty: bool = False
) -> str:
    """Set an editor property on a loaded asset, optionally saving it. Not placed actors (set_actor_properties)."""
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
    """Light editor/world summary (world name, actor count, engine version). Not an actor list."""
    return tool_json(send_command("get_editor_stats", {}), pretty=pretty)
