"""Start / stop HTTP listener and Slate tick hook."""

import socket
import threading
import time

import unreal

from listener.config import listener_bind_port_range
from listener.dispatch import handler_names
from listener.http import MCPHTTPServer, MCPRequestHandler
from listener.logutil import log_msg
from listener.state import command_queue, metrics, response_events, responses, responses_lock
from listener.status_window import MCPStatusWindow
from listener.tick import tick_handler


def abort_pending_commands(reason: str) -> int:
    """Fail in-flight and queued MCP commands before listener shutdown."""
    import queue

    from listener.state import accept_lock

    released = 0
    with accept_lock:
        unreal._mcp_in_flight = False
        unreal._mcp_in_flight_since = 0.0
        unreal._mcp_dispatching = False
        unreal._mcp_current_command = ""
        unreal._mcp_nested_since = 0.0
    with responses_lock:
        for req_id, ev in list(response_events.items()):
            responses[req_id] = {"success": False, "error": reason}
            ev.set()
            released += 1
        response_events.clear()

    while True:
        try:
            req_id, _command, _params = command_queue.get_nowait()
        except queue.Empty:
            break
        with responses_lock:
            responses[req_id] = {"success": False, "error": reason}
        released += 1

    return released


def _find_free_port() -> int:
    lo, hi = listener_bind_port_range()
    for port in range(lo, hi + 1):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            continue
    raise RuntimeError(f"No free port in range {lo}-{hi}")


def _on_python_shutdown() -> None:
    try:
        cleanup()
    except Exception:
        pass


def _close_on_python_shutdown() -> None:
    """Close the socket when UEFN stops Python (it does on every in-editor island switch).

    Left open, the socket outlives the interpreter with nothing serving it. The next
    island's listener then binds the same port beside it (SO_REUSEADDR) and Windows hands
    every connection to the dead one, so the listener reads offline until UEFN restarts.
    Once per interpreter: a new interpreter comes with a fresh ``unreal`` module.
    """
    if getattr(unreal, "_mcp_shutdown_handle", None) is not None:
        return
    register = getattr(unreal, "register_python_shutdown_callback", None)
    if register is None:
        return
    try:
        unreal._mcp_shutdown_handle = register(_on_python_shutdown)
    except Exception:
        pass


def start_listener(port: int = 0, show_status: bool = True) -> int:
    if unreal._mcp_server is not None:
        log_msg(f"Listener already running on port {unreal._mcp_bound_port}", "warning")
        if show_status and unreal._mcp_status_window:
            unreal._mcp_status_window.start()
        return unreal._mcp_bound_port

    if port == 0:
        port = _find_free_port()

    # ThreadingHTTPServer: each request gets its own worker thread so concurrent
    # MCP calls (and the periodic heartbeat GET) no longer serialize while a POST
    # blocks on Event.wait() for the tick to produce its result. The editor tick
    # remains the single main-thread serialization point, which is correct.
    server = MCPHTTPServer(("127.0.0.1", port), MCPRequestHandler)
    server.daemon_threads = True
    server.allow_reuse_address = True
    unreal._mcp_server = server
    unreal._mcp_bound_port = port

    unreal._mcp_server_thread = threading.Thread(
        target=unreal._mcp_server.serve_forever, daemon=True,
    )
    unreal._mcp_server_thread.start()

    if unreal._mcp_tick_handle is None:
        unreal._mcp_tick_handle = unreal.register_slate_post_tick_callback(tick_handler)
    _close_on_python_shutdown()

    metrics["started_at"] = time.time()

    log_msg(f"Listener started on http://127.0.0.1:{port}")
    log_msg(f"Registered {len(handler_names())} command handlers")

    if show_status:
        win = unreal._mcp_status_window
        if win is not None and win.is_alive() and getattr(win, "_window", None) is not None:
            win.start()
        else:
            unreal._mcp_status_window = MCPStatusWindow()
            unreal._mcp_status_window.start()

    return port


def stop_listener() -> None:
    if unreal._mcp_server is None:
        log_msg("Listener is not running", "warning")
        return

    server = unreal._mcp_server
    abort_pending_commands("Listener restarting — retry in a few seconds")
    try:
        server.shutdown()
        if unreal._mcp_server_thread is not None:
            unreal._mcp_server_thread.join(timeout=3.0)
    finally:
        # shutdown() only ends serve_forever; the listening socket stays bound until
        # server_close(). On Windows, SO_REUSEADDR then lets the next start bind the
        # same port beside the orphan, and connections that land on it hang.
        try:
            server.server_close()
        except OSError:
            pass

    unreal._mcp_server = None
    unreal._mcp_server_thread = None
    log_msg(f"Listener stopped (was on port {unreal._mcp_bound_port})")
    unreal._mcp_bound_port = 0
    metrics["started_at"] = 0.0
    metrics["last_client_ping"] = 0.0


def cleanup() -> None:
    stop_listener()
    if unreal._mcp_tick_handle is not None:
        unreal.unregister_slate_post_tick_callback(unreal._mcp_tick_handle)
        unreal._mcp_tick_handle = None


def restart_listener(port: int = 0) -> int:
    """Match classic ``uefn_listener.restart_listener`` — keep existing status window thread."""
    stop_listener()
    time.sleep(0.5)
    return start_listener(port, show_status=False)
