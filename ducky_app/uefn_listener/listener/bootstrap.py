"""First import: register commands, replace stale listener, start server."""

import os
import traceback

import unreal

from listener.logutil import log_msg
from listener.runtime import start_listener, stop_listener


def run() -> None:
    import listener.state  # noqa: F401 — attach shared state to unreal
    import listener.handlers  # noqa: F401 — register commands

    try:
        if unreal._mcp_server is not None:
            log_msg("Previous listener detected — replacing")
            try:
                # shutdown() + join() (not just server_close()) so the old
                # serve_forever thread and its socket actually release before
                # we bind a new one — otherwise both leak on every hot-reload.
                stop_listener()
            except Exception:
                pass
            unreal._mcp_server = None
            unreal._mcp_server_thread = None
            unreal._mcp_bound_port = 0

        _old_tick = unreal._mcp_tick_handle
        if _old_tick is not None:
            unreal.unregister_slate_post_tick_callback(_old_tick)
            unreal._mcp_tick_handle = None

        # In-editor Tk popup off by default — use UEFN-Ducky panel for metrics. Set
        # UEFN_DUCKY_STATUS_WINDOW=1 to restore the floating window (debug only).
        _show = os.environ.get("UEFN_DUCKY_STATUS_WINDOW", "").strip().lower() in ("1", "true", "yes")
        start_listener(show_status=_show)
    except Exception as e:
        unreal.log_error(f"[MCP] Failed to start listener: {e}")
        traceback.print_exc()
