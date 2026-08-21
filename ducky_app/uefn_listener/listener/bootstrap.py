"""First import: register commands, replace stale listener, start server.

Coexistence with Epic UEFN MCP Toolsets (port 8000): start Ducky on 4200 only.
Never thrash Epic's MCP when it is already listening — StartServer / settings
flips from here interfere with Beta Access → UEFN MCP Toolsets.
"""

import os
import socket
import traceback

import unreal

from listener.logutil import log_msg
from listener.runtime import start_listener, stop_listener


def _epic_mcp_tcp_up(host: str = "127.0.0.1", port: int = 8000, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def run(*, ensure_epic: bool | None = None) -> None:
    """Start the Ducky listener.

    ``ensure_epic``:
      - None / auto: only touch Epic MCP if :8000 is down
      - False: never call Epic StartServer (preferred when Toolsets already own MCP)
      - True: always try StartServer (legacy)
    """
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

        env = (os.environ.get("UEFN_DUCKY_ENSURE_EPIC") or "").strip().lower()
        if ensure_epic is None:
            if env in ("0", "false", "no", "never"):
                ensure_epic = False
            elif env in ("1", "true", "yes", "always"):
                ensure_epic = True
            else:
                # auto: do not interfere when Epic UEFN MCP Toolsets already bound :8000
                ensure_epic = not _epic_mcp_tcp_up()
        if ensure_epic:
            try:
                _ensure_epic_mcp()
            except Exception:
                pass
        elif _epic_mcp_tcp_up():
            log_msg("Epic MCP already on :8000 — Ducky listener coexistence (skip StartServer)")
    except Exception as e:
        unreal.log_error(f"[MCP] Failed to start listener: {e}")
        traceback.print_exc()


def _ensure_epic_mcp() -> None:
    """Start Epic's in-editor MCP (:8000) only when it is not already up."""
    if _epic_mcp_tcp_up():
        log_msg("Epic MCP already listening — skip StartServer")
        return
    try:
        world = unreal.EditorLevelLibrary.get_editor_world()
    except Exception:
        world = None
    try:
        unreal.SystemLibrary.execute_console_command(world, "ModelContextProtocol.StartServer")
        log_msg("Epic MCP StartServer")
    except Exception:
        pass
    # ponytail: named CDOs only — dir(unreal) is tens of thousands of types.
    # Only flip Auto Start when currently False; never fight Toolsets that already own MCP.
    for name in (
        "ModelContextProtocolEditorSettings",
        "ModelContextProtocolSettings",
        "MCPEditorSettings",
        "UEFNMcpSettings",
        "UEFNMcpToolsetsSettings",
    ):
        cls = getattr(unreal, name, None)
        if cls is None:
            continue
        try:
            obj = unreal.get_default_object(cls)
        except Exception:
            continue
        for attr in ("auto_start_server", "b_auto_start_server", "auto_start"):
            try:
                if getattr(obj, attr) is False:
                    setattr(obj, attr, True)
                    log_msg(f"Epic MCP {name}.{attr}=True")
            except Exception:
                pass
