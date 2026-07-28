"""Editor control tools: console, PIE, screenshots, saving, properties."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from backend.bridge import send_command
from backend.json_util import tool_json
from backend.tools.plugin_gate import plugin_mcp_tool

# Host-side poll budget — leave headroom under the listener's 30s HTTP timeout
# for each individual poll round-trip.
_SCREENSHOT_POLL_INTERVAL_SEC = 0.15
_SCREENSHOT_HOST_BUDGET_SEC = 28.0
_SCREENSHOT_POLL_TIMEOUT_SEC = 8.0


def _panel_media_server_reachable() -> bool:
    """True when the panel GUI HTTP server (media previews) answers on loopback."""
    try:
        from frontend.settings import PANEL_LISTENER_PORT

        port = int(PANEL_LISTENER_PORT) - 1
        req = urllib.request.Request(f"http://127.0.0.1:{port}/", method="GET")
        with urllib.request.urlopen(req, timeout=0.35) as resp:
            return int(getattr(resp, "status", 200) or 200) < 500
    except Exception:
        return False


def _project_root_configured() -> str:
    try:
        from frontend.settings import PanelSettings

        return (PanelSettings.load().uefn_project_root or "").strip()
    except Exception:
        return ""


def _enrich_screenshot(result: dict[str, Any]) -> dict[str, Any]:
    """Attach media_url + project DuckyCaptures copy; keep base64 out of results.

    Agents must use the project ``path`` for file work (import, copy, py). AppData
    ``capture_path`` / ``media_url`` are for chat UI preview only — agents do not
    have reliable access to AppData folders.
    """
    if not isinstance(result, dict):
        return result
    path = (result.get("path") or "").strip()
    if not path:
        return result
    src = Path(path)
    if not src.is_file():
        return {**result, "capture_error": f"Screenshot file missing on disk: {path}"}
    try:
        from frontend.ui_web.tool_captures import save_capture_for_agents

        raw = src.read_bytes()
        if not raw:
            return {**result, "capture_error": "Screenshot file is empty"}
        saved = save_capture_for_agents(raw, prefix="uefn_viewport")
        out = {**result}
        # Prefer DuckyCaptures when project root is set; else keep UE Screenshots path.
        project_path = str(saved.get("path") or "")
        project_root = _project_root_configured()
        mirrored = bool(project_path and Path(project_path).is_file() and "DuckyCaptures" in project_path.replace("\\", "/"))
        if mirrored:
            out["path"] = project_path
            out["ue_screenshot_path"] = path
        else:
            out["path"] = path
            if not project_root:
                out["project_mirror_warning"] = (
                    "uefn_project_root is not set — screenshot was not mirrored to "
                    "Saved/DuckyCaptures. Set the project in UEFN-Ducky Settings so "
                    "agents get a workspace-readable path."
                )
            else:
                out["project_mirror_warning"] = (
                    "Failed to mirror screenshot into Saved/DuckyCaptures; "
                    "using UE Screenshots path."
                )

        capture_path = str(saved.get("capture_path") or "")
        media_url = str(saved.get("media_url") or "")
        out["capture_path"] = capture_path
        out["capture_filename"] = saved.get("filename") or ""
        out["bytes"] = saved.get("bytes")

        if media_url and _panel_media_server_reachable():
            out["media_url"] = media_url
        elif media_url:
            out["media_url"] = ""
            out["preview_error"] = (
                "Panel media server is not reachable on loopback "
                f"(expected port for /tool-captures/). Preview file kept at {capture_path or 'AppData'}; "
                "open the UEFN-Ducky panel GUI to serve media_url."
            )
        else:
            out["media_url"] = ""

        hints = [
            "Use project path for file work; media_url/capture_path are AppData preview-only. "
            "Image is also returned as MCP image content when available."
        ]
        if out.get("project_mirror_warning"):
            hints.append(str(out["project_mirror_warning"]))
        if out.get("preview_error"):
            hints.append(str(out["preview_error"]))
        out["hint"] = " ".join(hints)
        out["status"] = "completed"
        return out
    except Exception as exc:
        return {**result, "capture_error": str(exc)[:200], "status": "failed"}


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


def _await_screenshot_result(
    width: int,
    height: int,
    filename: str,
) -> dict[str, Any]:
    """Capture screenshot; prefer single deferred listener wait, poll as fallback."""
    # Listener wait=True holds the HTTP request across ticks (packaged MCP compat).
    started = send_command(
        "take_high_res_screenshot",
        {"width": width, "height": height, "filename": filename, "wait": True},
        timeout=max(30.0, _SCREENSHOT_HOST_BUDGET_SEC + 2.0),
    )
    if not isinstance(started, dict):
        raise RuntimeError(f"Unexpected screenshot response: {started!r}")

    # Strip internal defer marker if a newer host somehow sees it.
    started.pop("_ducky_defer", None)

    status = str(started.get("status") or "").strip().lower()
    # Legacy listener / deferred completion: returned path immediately.
    if started.get("path") and status in ("", "completed"):
        started.setdefault("status", "completed")
        return started
    if status == "completed" and started.get("path"):
        return started
    if status in ("failed", "timed_out"):
        return started

    capture_id = str(started.get("capture_id") or "").strip()
    if not capture_id:
        if not started.get("path"):
            started = {
                **started,
                "status": "failed",
                "error": started.get("hint")
                or "Screenshot taken but path not resolved",
            }
        return started

    deadline = time.time() + _SCREENSHOT_HOST_BUDGET_SEC
    last: dict[str, Any] = started
    while time.time() < deadline:
        time.sleep(_SCREENSHOT_POLL_INTERVAL_SEC)
        remaining = max(0.5, deadline - time.time())
        poll_timeout = min(_SCREENSHOT_POLL_TIMEOUT_SEC, remaining)
        try:
            last = send_command(
                "poll_screenshot_capture",
                {"capture_id": capture_id},
                timeout=poll_timeout,
            )
        except TimeoutError:
            continue
        if not isinstance(last, dict):
            continue
        st = str(last.get("status") or "").strip().lower()
        if st == "completed" and last.get("path"):
            return last
        if st in ("failed", "timed_out"):
            return last
    return {
        **(last if isinstance(last, dict) else {}),
        "status": "timed_out",
        "capture_id": capture_id,
        "error": f"Screenshot timed out after {_SCREENSHOT_HOST_BUDGET_SEC:.0f}s waiting for capture",
    }


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
    width: int = 1920, height: int = 1080, filename: str = "", pretty: bool = False
) -> Any:
    """Capture a high-resolution screenshot of the active viewport.

    Returns project ``path`` (under ``Saved/DuckyCaptures`` when project root is
    set, else ``Saved/Screenshots``) + ``media_url`` for panel preview, and an
    MCP image content block so vision-capable agents can see the capture.
    Use ``path`` for any file work; ``media_url`` / ``capture_path`` are
    AppData preview-only. Never Bash-find for ``uefn_ducky_screenshot.png``.
    """
    result = _await_screenshot_result(width, height, filename)
    if not isinstance(result, dict):
        return tool_json(result, pretty=pretty)

    status = str(result.get("status") or "").strip().lower()
    if status in ("failed", "timed_out") or not result.get("path"):
        err = str(result.get("error") or result.get("hint") or "Screenshot capture failed")
        out = {
            **result,
            "ok": False,
            "success": False,
            "status": status or "failed",
            "error": err,
            "hint": (
                "Do not Bash-find for the PNG. Retry take_high_res_screenshot once; "
                "ensure the UEFN viewport is visible and the listener is online."
            ),
        }
        return tool_json(out, pretty=pretty)

    result = _enrich_screenshot(result)
    if result.get("capture_error") and not Path(str(result.get("path") or "")).is_file():
        result = {**result, "ok": False, "success": False, "status": "failed"}
        return tool_json(result, pretty=pretty)
    return _screenshot_mcp_payload(result, pretty=pretty)


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
