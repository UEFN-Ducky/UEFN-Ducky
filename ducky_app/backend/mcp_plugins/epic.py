"""Official in-editor Unreal MCP (nested under uefn-ducky).

Epic binds http://127.0.0.1:8000/mcp. Client config lives in ducky.db
(mcp.json is an export) — never island-root .mcp.json. Ducky starts the server
from the listener once Python is on; user just enables the project in the panel.

UEFN and Unreal Editor 5.8+ ship the same plugin (server ``unreal-mcp``, same
default port), so the process that owns the port decides which editor Ducky is
talking to: island work never goes to Unreal Editor, and Unreal project work
never goes to UEFN. Unreal Editor belongs on its own port (8001): Ducky looks for
it there, and moves it there itself when it finds it on UEFN's port.
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
# ponytail: fixed port, a setting when someone needs 8001 for something else.
UNREAL_EDITOR_MCP_PORT = 8001

EDITOR_UEFN = "uefn"
EDITOR_UNREAL = "unreal"

# TCP probe must be well under the old 45s MCP initialize hang.
_PROBE_TIMEOUT_SEC = 0.75
_PROBE_CACHE_SEC = 5.0
# Finding the port's owner walks every socket on the machine; an editor restart
# closes the port first, which clears this cache.
_OWNER_CACHE_SEC = 30.0
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

EPIC_MCP_SETUP_STEPS: tuple[str, ...] = (
    "Enable this island in Ducky. That turns on Python and starts Epic MCP.",
    "Open the project in UEFN (restart once if Epic MCP is still off).",
    "In Settings → MCPs keep only one HTTP server on port 8000 "
    "(UEFN MCP (Epic) / unreal-mcp). Disable any duplicate custom entry on the same port.",
    "If port 8000 is taken by something else, set Editor Preferences → Model Context Protocol port, "
    "then Settings → MCPs unreal-mcp URL to match.",
)

UNREAL_MCP_SETUP_STEPS: tuple[str, ...] = (
    'In Unreal Editor 5.8 or newer: Edit → Plugins → enable "Unreal MCP" and "All Toolsets", '
    "then restart the editor.",
    "Type ModelContextProtocol.StartServer in the editor console. Ducky finds the server on "
    f"port 8000 or {UNREAL_EDITOR_MCP_PORT}; no client config files are needed.",
    f"Once connected, Ducky moves Unreal Editor's MCP to port {UNREAL_EDITOR_MCP_PORT} and turns on "
    "Auto Start Server, so after one restart it starts by itself and never blocks UEFN on 8000.",
)

UNREAL_MOVED_NOTE = (
    f"Unreal Editor is on UEFN's port 8000. Ducky set it to {UNREAL_EDITOR_MCP_PORT}: "
    "restart Unreal Editor once and both editors can run together."
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

_probe_cache: dict[str, Any] = {"at": 0.0, "root": "", "result": None}
# port -> {at, name, pid, started}
_owner_cache: dict[int, dict[str, Any]] = {}


def epic_mcp_url() -> str:
    """UEFN's URL: the nested MCP row, else the catalog default.

    A row pointed at Unreal Editor's port (people set it by hand before Ducky knew
    both ports) still means UEFN's default port: the probe finds Unreal Editor itself.
    """
    try:
        from backend.mcp_plugins.store import load_plugin_manifest, resolve_server_block

        manifest = load_plugin_manifest(EPIC_MCP_SERVER_ID)
        if manifest:
            block = resolve_server_block(manifest)
            url = str(block.get("url") or "").strip()
            if url:
                if _host_port(url)[1] == UNREAL_EDITOR_MCP_PORT:
                    return with_port(url, _host_port(EPIC_MCP_DEFAULT_URL)[1])
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


def _host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port:
        return host, parsed.port
    return host, 443 if (parsed.scheme or "").lower() == "https" else 80


def with_port(url: str, port: int) -> str:
    parsed = urlparse(url)
    return parsed._replace(netloc=f"{parsed.hostname or '127.0.0.1'}:{port}").geturl()


def tcp_probe_url(url: str, *, timeout: float = _PROBE_TIMEOUT_SEC) -> bool:
    try:
        with socket.create_connection(_host_port(url), timeout=timeout):
            return True
    except OSError:
        return False


def editor_of_process(name: str) -> str:
    """``uefn`` / ``unreal`` for an editor's image name, ``other`` for any other program, '' unknown."""
    low = (name or "").strip().lower()
    if not low:
        return ""
    if low.startswith("unrealeditorfortnite"):
        return EDITOR_UEFN
    if low.startswith("unrealeditor"):
        return EDITOR_UNREAL
    return "other"


def editor_label(editor: str) -> str:
    return "Unreal Editor" if editor == EDITOR_UNREAL else "UEFN"


def _listening_process(port: int) -> tuple[str, int, float]:
    """(image name, pid, start time) of the local process listening on ``port``; ('', 0, 0) unknown."""
    try:
        import psutil

        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port and conn.pid:
                proc = psutil.Process(conn.pid)
                return proc.name(), conn.pid, proc.create_time()
    except Exception:
        pass
    return "", 0, 0.0


def _same_process(pid: int, started: float) -> bool:
    try:
        import psutil

        return bool(pid) and psutil.Process(pid).create_time() == started
    except Exception:
        return False


def _port_owner(port: int) -> str:
    # One editor can close and another take the port between two probes, so a cached
    # owner only counts while that same process is still running.
    now = time.time()
    hit = _owner_cache.get(port) or {}
    if (
        hit
        and now - float(hit.get("at") or 0) < _OWNER_CACHE_SEC
        and _same_process(int(hit.get("pid") or 0), float(hit.get("started") or 0))
    ):
        return str(hit.get("name") or "")
    name, pid, started = _listening_process(port)
    _owner_cache[port] = {"at": now, "name": name, "pid": pid, "started": started}
    return name


def expected_editor(project_root: str) -> str:
    """The editor a project's work belongs in.

    ``uefn`` for an island (``*.uefnproject``), ``unreal`` for an Unreal Engine project
    (``*.uproject``), '' when either is fine (no project, a plain code folder).
    """
    root = (project_root or "").strip()
    if not root:
        return ""
    try:
        from frontend.project_kind import has_uefnproject, has_uproject

        if has_uefnproject(root):
            return EDITOR_UEFN
        if has_uproject(root):
            return EDITOR_UNREAL
    except Exception:
        pass
    return ""


def _panel_project_root() -> str:
    try:
        from frontend.settings import PanelSettings

        return (PanelSettings.load().uefn_project_root or "").strip()
    except Exception:
        return ""


def _blocked_steps(reason: str, *, expected: str, editor: str, port: int, process: str) -> list[str]:
    if reason == "port_taken":
        return [
            f"Port {port} is used by {process}, not an Unreal editor, so the editor's MCP could not start there.",
            f"Close {process}, or set the editor's Server Port Number (Editor Preferences → General → "
            "Model Context Protocol) to a free port and Settings → MCPs → unreal-mcp URL to match.",
        ]
    if expected == EDITOR_UNREAL:
        return [
            f"Port {port} is held by UEFN, not Unreal Editor, so Ducky sends no Unreal project work there.",
            f"Start Unreal Editor's MCP (ModelContextProtocol.StartServer in its console); Ducky looks "
            f"for it on port {UNREAL_EDITOR_MCP_PORT}.",
        ]
    return [
        f"Port {port} is held by {editor_label(editor)}, not UEFN, so Ducky sends no island work there.",
        UNREAL_MOVED_NOTE,
        "Then type ModelContextProtocol.StartServer in UEFN's console, or reopen the island, "
        "so UEFN's MCP takes the port.",
    ]


def probe_epic_mcp(*, ttl_sec: float = _PROBE_CACHE_SEC, project_root: str | None = None) -> dict[str, Any]:
    """Cheap status for panel + agents. TCP only — never MCP initialize.

    ``project_root`` (default: the panel project) says which editor the work belongs
    in. ``epic_mcp_editor`` is the editor holding the port, '' when unknown. A port held
    by the other editor reads ``other_editor``, one held by any other program
    ``port_taken``; both are offline so no call reaches the wrong program.
    """
    now = time.time()
    cached = _probe_cache.get("result")
    fresh = isinstance(cached, dict) and now - float(_probe_cache.get("at") or 0) < ttl_sec
    if fresh and (project_root is None or _probe_cache.get("root") == project_root.strip()):
        return dict(cached)

    root = _panel_project_root() if project_root is None else project_root.strip()
    expected = expected_editor(root)
    uefn_url = epic_mcp_url()
    unreal_url = with_port(uefn_url, UNREAL_EDITOR_MCP_PORT)
    # Islands only ever look at UEFN's port; Unreal projects try Unreal's port first.
    if expected == EDITOR_UEFN:
        candidates = [uefn_url]
    elif expected == EDITOR_UNREAL:
        candidates = [unreal_url, uefn_url]
    else:
        candidates = [uefn_url, unreal_url]
    # (url, reason, editor, process) of the first port that answered but does not fit.
    blocked: tuple[str, str, str, str] | None = None
    picked: tuple[str, str, str] | None = None
    if epic_mcp_enabled():
        for url in candidates:
            host, port = _host_port(url)
            if not tcp_probe_url(url):
                _owner_cache.pop(port, None)
                continue
            process = _port_owner(port) if host.lower() in _LOOPBACK_HOSTS else ""
            found = editor_of_process(process)
            if found == "other":
                blocked = blocked or (url, "port_taken", "", process)
            elif found and expected and found != expected:
                blocked = blocked or (url, "other_editor", found, process)
            else:
                picked = (url, found, process)
                break
    if picked:
        url, editor, process = picked
        reason = ""
    elif blocked:
        url, reason, editor, process = blocked
    else:
        url, editor, process = candidates[0], "", ""
        reason = "unreachable" if epic_mcp_enabled() else "disabled"
    port = _host_port(url)[1]
    if not reason:
        steps: list[str] = []
    elif reason in ("disabled", "unreachable"):
        steps = list(UNREAL_MCP_SETUP_STEPS if expected == EDITOR_UNREAL else EPIC_MCP_SETUP_STEPS)
    else:
        steps = _blocked_steps(reason, expected=expected, editor=editor, port=port, process=process)
    result = {
        "epic_mcp_online": not reason,
        "epic_mcp_reason": reason,
        "epic_mcp_url": url,
        "epic_mcp_editor": editor,
        "epic_mcp_setup_steps": steps,
        # Unreal Editor on another port than its own: the pool moves it, the person restarts it once.
        "epic_mcp_note": UNREAL_MOVED_NOTE if editor == EDITOR_UNREAL and port != UNREAL_EDITOR_MCP_PORT else "",
    }
    _probe_cache["at"] = now
    _probe_cache["root"] = root
    _probe_cache["result"] = result
    return dict(result)


# Epic tools that only look: discovery tools, and inner tools named like reads.
_EPIC_READ_TOOLS = frozenset({"list_toolsets", "describe_toolset"})
_EPIC_READ_PREFIXES = ("list", "get", "describe", "find", "search", "query", "is", "has", "does")


def epic_tool_mutating(tool_name: str, arguments: dict[str, Any] | None = None) -> bool:
    """False for an Epic call that only reads the editor (``unreal__list_toolsets``, a
    ``call_tool`` whose ``tool_name`` is a Get/List/Find...), True otherwise."""
    if tool_name in _EPIC_READ_TOOLS:
        return False
    name = tool_name
    if tool_name == "call_tool":
        name = str((arguments or {}).get("tool_name") or "")
    leaf = re.split(r"[.:/]", name)[-1].strip()
    if not leaf:
        return True
    word = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", leaf).lower().split("_", 1)[0]
    return word not in _EPIC_READ_PREFIXES


async def call_epic_tool(tool_name: str, arguments: dict[str, Any] | None, fn: Any) -> Any:
    """Send one ``unreal__<tool_name>`` call through the editor gateway; ``fn()`` makes it."""
    from backend.bridge import editor_gateway

    return await editor_gateway.acall(
        editor_gateway.current_chat_id(),
        f"{EPIC_MCP_PREFIX}__{tool_name}",
        epic_tool_mutating(tool_name, arguments),
        fn,
    )


def invalidate_epic_mcp_probe() -> None:
    _probe_cache["at"] = 0.0
    _probe_cache["result"] = None
    _owner_cache.clear()


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
