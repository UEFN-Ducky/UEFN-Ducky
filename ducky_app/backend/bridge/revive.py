"""Bring the UEFN listener back without restarting UEFN.

Switching island in UEFN often opens it with Python off: ForceEnablePython never runs,
so neither do the init scripts that start Ducky, and the listener stays offline until
UEFN restarts. Epic UEFN MCP (:8000) is up by then and can turn Python on for the open
project, which runs those init scripts and starts the listener.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

_PYTHON_TOOLSET = "ValkyrieToolset.ValkyriePythonToolset"
_EPIC_CALL_TIMEOUT_SEC = 20.0
# UEFN MCP answers ~30 s before UEFN turns Python on itself, and the boot file then
# waits ~45 slow loading ticks; give a normal start this long before stepping in.
AUTO_REVIVE_GRACE_SEC = 60.0
_SESSION_RETRY_SEC = 2.0

_LAUNCH_HINT = (
    "In UEFN: Tools → Execute Python Script → %LOCALAPPDATA%/UEFN-Ducky/listener/launch_listener.py, "
    "or restart UEFN."
)


def _call_epic_tool(tool_name: str) -> str:
    """Sync nested ``unreal__call_tool`` on the Python toolset. Patchable in tests.

    A pooled session can be dead while Epic MCP answers TCP (UEFN switched island under
    it): "Could not enable Python ... session unavailable" was most auto restart errors
    (Oct 26-27 2026). Drop that session and try once more on a fresh one."""
    from backend.mcp_plugins.client_pool import get_plugin_pool
    from backend.mcp_plugins.epic import EPIC_MCP_SERVER_ID

    args = {"toolset_name": _PYTHON_TOOLSET, "tool_name": tool_name, "arguments": {}}
    pool = get_plugin_pool()
    try:
        return str(pool.run_sync(pool.call_tool("unreal__call_tool", args), timeout=_EPIC_CALL_TIMEOUT_SEC) or "")
    except RuntimeError as exc:
        if "session unavailable" not in str(exc):
            raise
    pool.forget(EPIC_MCP_SERVER_ID)
    time.sleep(_SESSION_RETRY_SEC)
    return str(pool.run_sync(pool.call_tool("unreal__call_tool", args), timeout=_EPIC_CALL_TIMEOUT_SEC) or "")


def _python_enabled_in_uefn() -> bool | None:
    """True / False from Epic MCP, None when it could not say."""
    text = _call_epic_tool("IsPythonEnabledInUEFN")
    m = re.search(r'returnValue\\?"\s*:\s*(true|false)', text)
    return None if m is None else m.group(1) == "true"


def _port_held_by_uefn(port: int) -> bool:
    """UEFN listens on ``port`` yet health fails: a socket from a stopped interpreter."""
    from backend.mcp_plugins.epic import EDITOR_UEFN, _listening_process, editor_of_process

    name, _pid, _started = _listening_process(port)
    return editor_of_process(name) == EDITOR_UEFN


def _refresh_boot_hooks() -> None:
    """Fortnite updates wipe the EditorToolset hook; put it back before Python starts."""
    try:
        from frontend.deploy import install_toolset_listener_boot, install_user_init_unreal

        install_toolset_listener_boot()
        install_user_init_unreal()
    except Exception:
        pass


def revive_listener(port: int) -> dict[str, Any]:
    """Restart the listener if it answers, else turn Python on in UEFN through Epic MCP.

    ``action``: ``reloaded`` | ``python_enabled`` | ``python_already_on`` | ``stale_socket`` |
    ``uefn_offline`` | ``failed``.
    """
    from backend.bridge import listener_get_health, post_command_to_listener

    health = listener_get_health(port)
    if health is not None and health.get("status") == "ok":
        try:
            post_command_to_listener(port, "reload_listener", timeout=120.0)
        except Exception as exc:
            return {"ok": False, "action": "failed", "message": f"Listener restart failed: {exc}"}
        return {"ok": True, "action": "reloaded", "message": "Listener restarted."}

    from backend.mcp_plugins.epic import invalidate_epic_mcp_probe, probe_epic_mcp

    invalidate_epic_mcp_probe()
    if not probe_epic_mcp().get("epic_mcp_online"):
        return {
            "ok": False,
            "action": "uefn_offline",
            "message": "UEFN MCP is offline — open a project in UEFN first. " + _LAUNCH_HINT,
        }

    _refresh_boot_hooks()
    try:
        enabled = _python_enabled_in_uefn()
        if enabled and _port_held_by_uefn(port):
            return {
                "ok": False,
                "action": "stale_socket",
                "message": (
                    f"A listener left over from the previous island still holds port {port} "
                    "(UEFN switched island without closing it). Restart UEFN once — from this "
                    "version on, Ducky closes it on every island switch."
                ),
            }
        if enabled:
            return {
                "ok": False,
                "action": "python_already_on",
                "message": "Python is on in UEFN but the listener did not start. " + _LAUNCH_HINT,
            }
        _call_epic_tool("EnablePythonInUEFN")
    except Exception as exc:
        return {"ok": False, "action": "failed", "message": f"Could not enable Python in UEFN: {exc}. " + _LAUNCH_HINT}
    return {
        "ok": True,
        "action": "python_enabled",
        "message": "Python enabled in UEFN — the listener starts in a few seconds.",
    }


class AutoRevive:
    """Panel-poll watchdog: one revive per offline spell while UEFN MCP is up.

    A spell ends when the listener comes online or UEFN MCP goes away (UEFN closed or
    switching island), so the next island gets its own try.
    """

    def __init__(self, *, grace_sec: float = AUTO_REVIVE_GRACE_SEC) -> None:
        self._grace = grace_sec
        self._lock = threading.Lock()
        self._offline_since = 0.0
        self._tried = False

    def observe(self, status: dict[str, Any], port: int, *, now: float | None = None) -> bool:
        """Feed a listener status; starts a background revive when due. True when it did."""
        t = time.time() if now is None else now
        down = not status.get("online") and bool(status.get("epic_mcp_online"))
        with self._lock:
            if not down:
                self._offline_since = 0.0
                self._tried = False
                return False
            if not self._offline_since:
                self._offline_since = t
            if self._tried or t - self._offline_since < self._grace:
                return False
            self._tried = True
        threading.Thread(target=self._run, args=(port,), name="ducky-listener-revive", daemon=True).start()
        return True

    @staticmethod
    def _run(port: int) -> None:
        try:
            result = revive_listener(port)
        except Exception as exc:
            result = {"ok": False, "message": str(exc)}
        # UEFN closed between the status poll and the try: status, not an error.
        if result.get("ok") or result.get("action") == "uefn_offline":
            return
        try:
            from frontend.error_log import record_error

            record_error(error_source("listener_revive", result), f"Auto restart: {result.get('message', '')}")
        except Exception:
            pass


def error_source(prefix: str, result: dict[str, Any]) -> str:
    """Error code naming how a revive ended (``listener_revive.stale_socket``): plain
    "listener" (8 on 4 PCs, Oct 26-27 2026) could not say which step failed."""
    return f"{prefix}.{result.get('action') or 'failed'}"
