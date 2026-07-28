"""HTTP client to the UEFN listener, workspace path helpers, and MCP heartbeat."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from backend.env_compat import env_int

DEFAULT_PORT = env_int("PORT", 4200)
MAX_PORT = 4267
REQUEST_TIMEOUT = 30.0

_discovered_port: Optional[int] = None
_pinned_port: Optional[int] = None

_DISCOVERY_BUDGET_SEC = 3.0
_DISCOVERY_PING_TIMEOUT = 0.2

# ---------------------------------------------------------------------------
# Read-through cache (host process only)
# ---------------------------------------------------------------------------

_CACHEABLE_COMMANDS: dict[str, float] = {
    "get_project_info": 30.0,
    "get_level_info": 15.0,
    "does_asset_exist": 10.0,
    "ping": 5.0,
}

_READ_COMMANDS = frozenset(
    {
        "ping",
        "status",
        "get_log",
        "get_editor_log",
        "get_all_actors",
        "get_selected_actors",
        "get_actor_properties",
        "list_assets",
        "get_asset_info",
        "get_selected_assets",
        "does_asset_exist",
        "search_assets",
        "get_project_info",
        "get_level_info",
        "get_viewport_camera",
    }
)

_response_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_cache_lock = threading.Lock()

# Serialize every command sent to the listener. FastMCP runs tool calls on a
# worker-thread pool; without this lock, parallel MCP tools open N concurrent
# POSTs. The listener also rejects concurrent accepts (503), but the host must
# queue and retry so agents get success instead of races / wedges.
_listener_lock = threading.Lock()
# Last time any POST command got a response — status polls use this to skip
# their ping while agents are actively (and successfully) running commands.
_last_post_ok_at = 0.0


_POST_TIMEOUT_COOLDOWN_SEC = 2.0
_BUSY_RETRY_SLEEP_SEC = 0.15
_BUSY_WAIT_POLL_SEC = 0.1


def _record_bridge_error(message: str) -> None:
    """Persist a bridge-side error to the shared appdata error log (best-effort)."""
    try:
        from frontend.error_log import record_error

        record_error("bridge", message)
    except Exception:
        pass


def _listener_is_busy(port: int) -> bool:
    """True when GET health reports an in-flight / queued command."""
    health = listener_get_health(port, timeout=0.5)
    if not health:
        return False
    return bool(health.get("busy")) or int(health.get("queue_size") or 0) > 0


def _wait_listener_idle(port: int, *, deadline: float) -> None:
    """Poll GET / until not busy, or until deadline (then POST anyway)."""
    while time.time() < deadline:
        if not _listener_is_busy(port):
            return
        time.sleep(_BUSY_WAIT_POLL_SEC)


def _post_json_locked(
    port: int,
    command: str,
    params: Optional[dict],
    *,
    timeout: float,
) -> dict:
    """POST one command under ``_listener_lock``, waiting out 503 busy replies.

    Caller must already hold ``_listener_lock``. Retries 503 until the overall
    timeout budget is spent so parallel MCP tools serialize instead of failing.
    """
    url = f"http://127.0.0.1:{port}"
    payload = json.dumps({"command": command, "params": params or {}}).encode()
    deadline = time.time() + max(timeout, 1.0)
    _wait_listener_idle(port, deadline=deadline)

    last_http: Optional[urllib.error.HTTPError] = None
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            if last_http is not None:
                _handle_listener_http_error(last_http)
            raise TimeoutError(f"Command '{command}' timed out waiting for listener idle")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=max(remaining, 0.5)) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 503:
                last_http = e
                time.sleep(_BUSY_RETRY_SLEEP_SEC)
                continue
            _handle_listener_http_error(e)
            raise
        except Exception as e:
            if "timed out" in str(e).lower():
                time.sleep(_POST_TIMEOUT_COOLDOWN_SEC)
                raise TimeoutError(f"Command '{command}' timed out after {timeout}s") from e
            raise

# ---------------------------------------------------------------------------
# Port discovery
# ---------------------------------------------------------------------------


def set_port_override(port: Optional[int]) -> None:
    """Pin the listener port (stdio `--port`) or clear override (None = auto-discover)."""
    global _discovered_port, _pinned_port
    _pinned_port = port
    _discovered_port = port


def configured_listener_port() -> int:
    """Listener port from ``--port`` / cache, or :data:`DEFAULT_PORT` before network discovery."""
    if _pinned_port is not None:
        return _pinned_port
    if _discovered_port is not None:
        return _discovered_port
    return DEFAULT_PORT


def _listener_unreachable_error() -> ConnectionError:
    return ConnectionError(
        f"UEFN listener offline on port {configured_listener_port()}. "
        "Do NOT retry until online. Verse file edits / workspace_list_verse_errors / digests / "
        "ducky_* / blender_* work without it. "
        "Start listener: Deploy in the panel, or Tools → Execute Python Script → "
        "ducky_app/uefn_listener/launch_listener.py"
    )


def discover_port() -> int:
    """Find the listener by scanning the port range.

    When port is pinned via :func:`set_port_override`, only that port is tried (fast fail).
    Otherwise scans DEFAULT_PORT..MAX_PORT with a short time budget.
    """
    global _discovered_port

    if _discovered_port is not None:
        if _ping_port(_discovered_port):
            return _discovered_port
        if _pinned_port is not None:
            raise _listener_unreachable_error()
        _discovered_port = None

    if _pinned_port is not None:
        if _ping_port(_pinned_port):
            _discovered_port = _pinned_port
            return _pinned_port
        raise _listener_unreachable_error()

    deadline = time.time() + _DISCOVERY_BUDGET_SEC
    for port in range(DEFAULT_PORT, MAX_PORT + 1):
        if time.time() >= deadline:
            break
        if _ping_port(port, timeout=_DISCOVERY_PING_TIMEOUT):
            _discovered_port = port
            return port

    raise ConnectionError(
        f"UEFN listener not found on ports {DEFAULT_PORT}-{MAX_PORT}. "
        "Start it in UEFN: Tools → Execute Python Script → ducky_app/uefn_listener/launch_listener.py, or Deploy in the panel"
    )


def _ping_port(port: int, *, timeout: float = 1.0) -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
            return body.get("status") == "ok"
    except Exception:
        return False


def listener_ping_port(port: int) -> bool:
    """True if the UEFN HTTP listener answers GET / on ``port`` (does not change discovery cache)."""
    return _ping_port(port)


def _handle_listener_http_error(e: urllib.error.HTTPError) -> None:
    if e.code == 503:
        try:
            err_body = json.loads(e.read().decode())
            msg = err_body.get("error", "Listener busy")
        except Exception:
            msg = "Listener busy — one editor command at a time"
        from backend.listener_serial import BUSY_HINT

        raise RuntimeError(f"{msg}. {BUSY_HINT}") from e
    raise e


_HEALTH_FAIL_FAST_SEC = 0.35


def post_command_to_listener(
    port: int,
    command: str,
    params: Optional[dict] = None,
    *,
    timeout: float = 8.0,
) -> dict:
    """POST ``{"command":..., "params":...}`` to a listener on a fixed port (panel / tools).

    Returns the command ``result`` dict on success. Raises like :func:`send_command` on failure.
    """
    global _last_post_ok_at
    if listener_get_health(port, timeout=_HEALTH_FAIL_FAST_SEC) is None:
        raise ConnectionError(
            f"UEFN listener offline on port {port}. "
            "Do NOT retry until the listener is online — Verse file edits, digests, "
            "panel tools (ducky_*), and blender_* work without it. Use workspace_* for Verse errors."
        )
    t_lock0 = time.perf_counter()
    try:
        with _listener_lock:
            lock_wait_ms = (time.perf_counter() - t_lock0) * 1000.0
            t_http0 = time.perf_counter()
            body = _post_json_locked(port, command, params, timeout=timeout)
            http_ms = (time.perf_counter() - t_http0) * 1000.0
        _last_post_ok_at = time.time()
    except TimeoutError:
        raise
    except urllib.error.URLError as e:
        raise ConnectionError(f"UEFN listener not reachable on port {port}") from e
    try:
        from frontend.perf_trace import trace

        trace(
            "listener_cmd",
            command,
            http_ms,
            lock_wait_ms=round(lock_wait_ms, 3),
            port=port,
        )
    except Exception:
        pass
    if not body.get("success", False):
        error_msg = body.get("error", "Unknown error")
        tb = body.get("traceback", "")
        raise RuntimeError(f"UEFN command '{command}' failed: {error_msg}\n{tb}".strip())
    return body.get("result", {})


def seconds_since_last_post_ok() -> float:
    """Seconds since any listener POST succeeded (``inf`` if never).

    Status polls use this to skip their own ping while agents are actively
    running commands — a fresh success already proves the listener is alive,
    and an extra ping would queue behind agent work on the UEFN main thread.
    """
    if _last_post_ok_at <= 0:
        return float("inf")
    return max(0.0, time.time() - _last_post_ok_at)


def listener_get_health(port: int, timeout: float = 1.0) -> Optional[dict]:
    """GET ``http://127.0.0.1:port/`` JSON (same as MCP bridge heartbeat), or ``None`` if down."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


def _cache_key(command: str, params: Optional[dict]) -> tuple[str, str]:
    return command, json.dumps(params or {}, sort_keys=True, separators=(",", ":"))


def _invalidate_cache() -> None:
    with _cache_lock:
        _response_cache.clear()


def send_command(command: str, params: Optional[dict] = None, timeout: float = REQUEST_TIMEOUT) -> dict:
    """Send a command to the UEFN listener and return the result.

    Raises:
        ConnectionError: Listener is not running.
        RuntimeError: Command failed on the UEFN side.
        TimeoutError: Command timed out.
    """
    global _discovered_port, _last_post_ok_at

    cache_ttl = _CACHEABLE_COMMANDS.get(command)
    key = _cache_key(command, params)
    if cache_ttl is not None:
        now = time.time()
        with _cache_lock:
            hit = _response_cache.get(key)
            if hit is not None:
                if now - hit[0] < cache_ttl:
                    return hit[1]
                _response_cache.pop(key, None)
            # Cap growth: drop oldest expired entries opportunistically.
            if len(_response_cache) > 256:
                stale = [k for k, (ts, _) in _response_cache.items() if now - ts >= cache_ttl]
                for k in stale[:64]:
                    _response_cache.pop(k, None)

    # Always fail-fast when the configured/cached port is down — never sit on
    # REQUEST_TIMEOUT (30s) discovering or POSTing a dead listener.
    probe = configured_listener_port()
    if listener_get_health(probe, timeout=_HEALTH_FAIL_FAST_SEC) is not None:
        port = probe
        _discovered_port = probe
    elif _pinned_port is not None:
        _record_bridge_error(
            f"Listener not reachable for '{command}' (GET health failed on port {probe})"
        )
        raise _listener_unreachable_error()
    else:
        # Short discovery (~3s budget), then fail — never fall through to a 30s POST.
        _discovered_port = None
        try:
            port = discover_port()
        except ConnectionError:
            _record_bridge_error(f"Listener not reachable for '{command}' (discovery failed)")
            raise
    t_lock0 = time.perf_counter()
    try:
        with _listener_lock:
            lock_wait_ms = (time.perf_counter() - t_lock0) * 1000.0
            t_http0 = time.perf_counter()
            body = _post_json_locked(port, command, params, timeout=timeout)
            http_ms = (time.perf_counter() - t_http0) * 1000.0
        _last_post_ok_at = time.time()
    except TimeoutError as e:
        _record_bridge_error(str(e))
        raise
    except urllib.error.URLError as e:
        if _discovered_port is not None and _pinned_port is None:
            _discovered_port = None
        _record_bridge_error(f"Listener not reachable for '{command}': {e}")
        raise ConnectionError(
            "UEFN listener is not running. "
            "Start it in UEFN: Tools → Execute Python Script → ducky_app/uefn_listener/launch_listener.py, or Deploy in the panel"
        ) from e

    try:
        from frontend.perf_trace import trace

        trace(
            "listener_cmd",
            command,
            http_ms,
            lock_wait_ms=round(lock_wait_ms, 3),
            port=port,
        )
    except Exception:
        pass

    if not body.get("success", False):
        error_msg = body.get("error", "Unknown error")
        tb = body.get("traceback", "")
        raise RuntimeError(f"UEFN command '{command}' failed: {error_msg}\n{tb}".strip())

    result = body.get("result", {})

    if command not in _READ_COMMANDS:
        _invalidate_cache()
    elif cache_ttl is not None:
        with _cache_lock:
            _response_cache[key] = (time.time(), result)

    return result


def check_connection() -> str:
    try:
        port = discover_port()
        return f"Connected to UEFN on port {port}"
    except ConnectionError:
        return "NOT CONNECTED - UEFN listener is not running"
    except Exception as e:
        return f"Connection error: {e}"


# ---------------------------------------------------------------------------
# Heartbeat — periodic ping so the listener knows we're alive
# ---------------------------------------------------------------------------

_HEARTBEAT_INTERVAL = 10.0


def _heartbeat_loop() -> None:
    time.sleep(3.0)
    while True:
        try:
            port = discover_port()
            req = urllib.request.Request(f"http://127.0.0.1:{port}", method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as _resp:
                _resp.read(64)
        except Exception:
            pass
        time.sleep(_HEARTBEAT_INTERVAL)


threading.Thread(target=_heartbeat_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# Workspace file tools (host disk; VS Code extension sets UEFN_VSCODE_WORKSPACE_FOLDERS)
# ---------------------------------------------------------------------------


def _configured_project_roots() -> list[str]:
    """Project roots from env or panel settings (fallback when VS Code folders unset)."""
    roots: list[str] = []
    for key in ("UEFN_VSCODE_WORKSPACE_FOLDERS",):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, list):
            continue
        for item in data:
            if item:
                roots.append(os.path.realpath(os.path.abspath(str(item))))
    for key in ("UEFN_DUCKY_PROJECT_ROOT",):
        raw = os.environ.get(key, "").strip()
        if raw:
            roots.append(os.path.realpath(os.path.abspath(raw)))
    if not roots:
        try:
            from frontend.settings import PanelSettings

            panel_root = PanelSettings.load().uefn_project_root.strip()
            if panel_root:
                roots.append(os.path.realpath(os.path.abspath(panel_root)))
        except Exception:
            pass
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


def workspace_roots() -> list[str]:
    return _configured_project_roots()


def _is_under_root(candidate: str, root: str) -> bool:
    cand = os.path.realpath(os.path.abspath(candidate))
    root_r = os.path.realpath(os.path.abspath(root))
    try:
        return os.path.commonpath([cand, root_r]) == root_r
    except ValueError:
        return False


def resolve_workspace_path(relative_or_absolute: str) -> str:
    """Map user path to an absolute path confined to workspace roots."""
    roots = workspace_roots()
    if not roots:
        raise ValueError(
            "No workspace root configured. Set the project in UEFN-Ducky Settings → Agent, "
            "or set UEFN_VSCODE_WORKSPACE_FOLDERS / UEFN_DUCKY_PROJECT_ROOT."
        )
    p = (relative_or_absolute or "").strip()
    if not p:
        raise ValueError("Path must not be empty.")
    if os.path.isabs(p) or (len(p) > 2 and p[1] == ":"):
        full = os.path.realpath(os.path.abspath(p))
        for r in roots:
            if _is_under_root(full, r):
                return full
        raise ValueError(f"Path is outside allowed workspace folders: {relative_or_absolute!r}")
    primary = roots[0]
    full = os.path.realpath(os.path.abspath(os.path.join(primary, p)))
    if not _is_under_root(full, primary):
        raise ValueError(f"Path escapes workspace root: {relative_or_absolute!r}")
    # Models often prefix the root folder name ("Content/Verse" when the root IS
    # ...\Content). If the literal path is missing but the deduped one exists, use it.
    if not os.path.exists(full):
        root_name = os.path.basename(primary.rstrip("\\/")).lower()
        first, _, rest = p.replace("\\", "/").partition("/")
        if rest and first.lower() == root_name:
            alt = os.path.realpath(os.path.abspath(os.path.join(primary, rest)))
            alt_ok = os.path.exists(alt) or (
                not os.path.isdir(os.path.dirname(full)) and os.path.isdir(os.path.dirname(alt))
            )
            if _is_under_root(alt, primary) and alt_ok:
                return alt
    return full
