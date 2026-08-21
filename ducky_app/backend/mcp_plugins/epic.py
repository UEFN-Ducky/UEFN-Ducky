"""Official in-editor Unreal MCP (nested under uefn-ducky).

Epic binds http://127.0.0.1:8000/mcp. Client JSON lives in AppData mcp.json —
never island-root .mcp.json. Ducky starts the server from the listener once
Python is on; user just enables the project in the panel.
"""

from __future__ import annotations

import os
import re
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

EPIC_MCP_SERVER_ID = "unreal-mcp"
EPIC_MCP_DEFAULT_URL = "http://127.0.0.1:8000/mcp"
EPIC_MCP_PREFIX = "unreal"

# TCP probe must be well under the old 45s MCP initialize hang.
_PROBE_TIMEOUT_SEC = 0.75
_PROBE_CACHE_SEC = 5.0

EPIC_MCP_SETUP_STEPS: tuple[str, ...] = (
    "Enable this island in Ducky. That turns on Python and starts Epic MCP.",
    "Open the project in UEFN (restart once if Epic MCP is still off).",
    "In Settings → MCPs keep only one HTTP server on port 8000 "
    "(UEFN MCP (Epic) / unreal-mcp). Disable any duplicate custom entry on the same port.",
    "If port 8000 is taken by something else, set Editor Preferences → Model Context Protocol port, "
    "then Settings → MCPs unreal-mcp URL to match.",
)

# Ducky MCP tools that duplicate Epic's four toolsets — stop registering these.
EPIC_PRUNED_DUCKY_TOOLS: frozenset[str] = frozenset(
    {
        "list_entities",
        "get_entity_info",
        "list_scene_component_classes",
        "get_selected_entities",
        "select_entities",
        "create_entity",
        "set_entity_transform",
        "add_entity_component",
        "remove_entity_component",
        "set_entity_component_property",
        "get_entity_component_property",
        "rename_entity",
        "set_entity_parent",
        "duplicate_entity",
        "destroy_entity",
        "find_devices",
        "inspect_creative_device",
        "set_creative_device_fields",
        "list_creative_devices",
        "get_device_settings",
        "set_device_settings",
        "play_in_editor",
        "stop_pie",
    }
)

_probe_cache: dict[str, Any] = {"at": 0.0, "result": None}


def epic_mcp_url() -> str:
    """Live URL from AppData mcp.json, else the catalog default."""
    try:
        from backend.mcp_plugins.store import load_plugin_manifest, resolve_server_block

        manifest = load_plugin_manifest(EPIC_MCP_SERVER_ID)
        if manifest:
            block = resolve_server_block(manifest)
            url = str(block.get("url") or "").strip()
            if url:
                return url
    except Exception:
        pass
    return EPIC_MCP_DEFAULT_URL


def epic_mcp_enabled() -> bool:
    try:
        from backend.mcp_plugins.store import get_enabled_plugin_ids

        return EPIC_MCP_SERVER_ID in get_enabled_plugin_ids()
    except Exception:
        return False


def tcp_probe_url(url: str, *, timeout: float = _PROBE_TIMEOUT_SEC) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port:
        port = parsed.port
    elif (parsed.scheme or "").lower() == "https":
        port = 443
    else:
        port = 80
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_epic_mcp(*, ttl_sec: float = _PROBE_CACHE_SEC) -> dict[str, Any]:
    """Cheap status for panel + agents. TCP only — never MCP initialize."""
    now = time.time()
    cached = _probe_cache.get("result")
    if isinstance(cached, dict) and now - float(_probe_cache.get("at") or 0) < ttl_sec:
        return dict(cached)

    url = epic_mcp_url()
    enabled = epic_mcp_enabled()
    if not enabled:
        result = {
            "epic_mcp_online": False,
            "epic_mcp_reason": "disabled",
            "epic_mcp_url": url,
            "epic_mcp_setup_steps": list(EPIC_MCP_SETUP_STEPS),
        }
    elif tcp_probe_url(url):
        result = {
            "epic_mcp_online": True,
            "epic_mcp_reason": "",
            "epic_mcp_url": url,
            "epic_mcp_setup_steps": [],
        }
    else:
        result = {
            "epic_mcp_online": False,
            "epic_mcp_reason": "unreachable",
            "epic_mcp_url": url,
            "epic_mcp_setup_steps": list(EPIC_MCP_SETUP_STEPS),
        }
    _probe_cache["at"] = now
    _probe_cache["result"] = result
    return dict(result)


def invalidate_epic_mcp_probe() -> None:
    _probe_cache["at"] = 0.0
    _probe_cache["result"] = None


_EDITOR_INI = (
    Path(os.environ.get("LOCALAPPDATA") or "")
    / "UnrealEditorFortnite"
    / "Saved"
    / "Config"
    / "WindowsEditor"
    / "Editor.ini"
)
_AUTO_START_BLOCK = (
    "\n[/Script/ModelContextProtocolEditor.ModelContextProtocolEditorSettings]\n"
    "bAutoStartServer=True\n"
)


def ensure_editor_auto_start(ini_path: Path | None = None) -> bool:
    """Persist Epic Auto Start in Editor.ini. True if the file was written."""
    ini = ini_path or _EDITOR_INI
    if not ini.is_file():
        return False
    try:
        text = ini.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    flipped, n = re.subn(r"bAutoStartServer\s*=\s*False", "bAutoStartServer=True", text, flags=re.I)
    if n:
        ini.write_text(flipped, encoding="utf-8")
        return True
    if "bAutoStartServer=True" in text or "bAutoStartServer = True" in text:
        return False
    if not text.endswith("\n"):
        text += "\n"
    ini.write_text(text + _AUTO_START_BLOCK, encoding="utf-8")
    return True
