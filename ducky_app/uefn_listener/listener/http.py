"""HTTP surface: GET health, POST command."""

import json
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import BaseServer
from typing import Any

import unreal

from listener.accept_queue import (
    ACCEPT_QUEUE_MAX,
    busy_payload_dict,
    can_accept_queued_command,
    is_light_command,
)
from listener.config import HTTP_TIMEOUT_SEC, PROTOCOL_VERSION
from listener.dispatch import dispatch, handler_names
from listener.state import accept_lock, command_queue, response_events, responses, responses_lock

_CONN_ERRORS = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)


def _is_client_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, _CONN_ERRORS):
        return True
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (10053, 10054):
        return True
    return False


def _busy_payload() -> bytes:
    return json.dumps(
        busy_payload_dict(queue_size=command_queue.qsize(), max_queue=ACCEPT_QUEUE_MAX)
    ).encode()


class MCPHTTPServer(ThreadingHTTPServer):
    """Suppress WinError 10053 noise when reload_listener shuts down mid-request."""

    def handle_error(self, request: Any, client_address: Any) -> None:
        if _is_client_disconnect(sys.exc_info()[1]):
            return
        BaseServer.handle_error(self, request, client_address)


class MCPRequestHandler(BaseHTTPRequestHandler):
    # HTTP/1.1 enables persistent connections so the host can reuse one TCP
    # socket across commands. Every response below sends Content-Length, so the
    # client can frame replies without closing the connection.
    protocol_version = "HTTP/1.1"

    def _send_json(self, code: int, body: bytes) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError) as e:
            if not _is_client_disconnect(e):
                raise

    def do_GET(self) -> None:
        from listener.state import metrics

        metrics["last_client_ping"] = time.time()
        with accept_lock:
            qsize = command_queue.qsize()
            in_flight = bool(getattr(unreal, "_mcp_in_flight", False))
            dispatching = bool(getattr(unreal, "_mcp_dispatching", False))
            since = float(getattr(unreal, "_mcp_in_flight_since", 0.0) or 0.0)
            in_flight_age = (time.time() - since) if in_flight and since > 0 else 0.0
        body = json.dumps({
            "status": "ok",
            "version": PROTOCOL_VERSION,
            "port": unreal._mcp_bound_port,
            "commands": handler_names(),
            # Busy = a command is on the main thread or queued. Agents must not
            # treat this as "wedged" — wedged is GET ok + POST ping failing twice.
            "busy": in_flight or qsize > 0 or dispatching,
            "in_flight": in_flight,
            "dispatching": dispatching,
            "in_flight_age_sec": round(in_flight_age, 1),
            "queue_size": qsize,
            "max_queue": ACCEPT_QUEUE_MAX,
        }).encode()
        self._send_json(200, body)

    def _dispatch_light(self, command: str, params: dict) -> None:
        """Run a light command on this HTTP worker — never touches the editor slot."""
        try:
            result = dispatch(command, params if isinstance(params, dict) else {})
            payload = {"success": True, "result": result}
        except Exception as e:
            payload = {
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
            }
        self._send_json(200, json.dumps(payload).encode())

    def do_POST(self) -> None:
        from listener.state import metrics

        metrics["last_client_ping"] = time.time()
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(content_length)
        except _CONN_ERRORS:
            return
        except OSError as e:
            if _is_client_disconnect(e):
                return
            raise
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as e:
            self._send_json(400, json.dumps({"success": False, "error": f"Invalid JSON: {e}"}).encode())
            return

        command = body.get("command", "")
        params = body.get("params", {})
        if not command:
            self._send_json(400, json.dumps({"success": False, "error": "Missing 'command' field"}).encode())
            return

        # Light commands never occupy the editor in-flight slot.
        if is_light_command(str(command)):
            self._dispatch_light(str(command), params if isinstance(params, dict) else {})
            return

        # Reject while a hot-reload is swapping modules.
        if bool(getattr(unreal, "_mcp_reload_in_progress", False)):
            self._send_json(503, _busy_payload())
            return

        done = threading.Event()

        # Bounded queue: multiple agents enqueue in order; 503 only when full.
        with accept_lock:
            qsize = command_queue.qsize()
            if not can_accept_queued_command(queue_size=qsize, max_queue=ACCEPT_QUEUE_MAX):
                self._send_json(503, _busy_payload())
                return
            unreal._mcp_request_counter += 1
            req_id = f"req_{unreal._mcp_request_counter}_{time.time_ns()}"
            with responses_lock:
                response_events[req_id] = done
            # Tick clears _mcp_in_flight when the queue drains. Never clear it
            # here on timeout — that let the next parallel MCP tool enqueue while the
            # editor was still running a heavy wire/spawn and froze UEFN.
            # Tick self-heals orphaned in_flight after STUCK_INFLIGHT_SEC when idle.
            if not unreal._mcp_in_flight:
                unreal._mcp_in_flight = True
                unreal._mcp_in_flight_since = time.time()
            command_queue.put((req_id, command, params))

        if not done.wait(timeout=HTTP_TIMEOUT_SEC):
            with responses_lock:
                response_events.pop(req_id, None)
            self._send_json(
                504,
                json.dumps(
                    {
                        "success": False,
                        "error": (
                            f"Command '{command}' timed out — editor busy "
                            "(Verse compile / save / PIE can stall the tick). "
                            "Do NOT assume the listener is offline; wait and retry once."
                        ),
                    }
                ).encode(),
            )
            return

        with responses_lock:
            result = responses.pop(req_id, None)
        if result is None:
            self._send_json(
                500,
                json.dumps({"success": False, "error": f"Internal error: missing result for '{command}'"}).encode(),
            )
            return

        self._send_json(200, json.dumps(result).encode())

    def log_message(self, fmt: str, *args: Any) -> None:
        pass
