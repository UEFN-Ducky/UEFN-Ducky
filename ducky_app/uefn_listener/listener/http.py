"""HTTP surface: GET health, POST command."""

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import BaseServer
from typing import Any

import unreal

from listener.config import HTTP_TIMEOUT_SEC, PROTOCOL_VERSION
from listener.dispatch import handler_names
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
        {
            "success": False,
            "error": (
                "Listener busy — one editor command at a time. "
                "Wait for the previous MCP tool to finish before calling another wire/spawn/save."
            ),
            "queue_size": command_queue.qsize(),
        }
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

        now = time.time()
        metrics["last_client_ping"] = now
        with accept_lock:
            qsize = command_queue.qsize()
            in_flight = bool(getattr(unreal, "_mcp_in_flight", False))
            dispatching = bool(getattr(unreal, "_mcp_dispatching", False))
            since = float(getattr(unreal, "_mcp_in_flight_since", 0.0) or 0.0)
            in_flight_age = (now - since) if in_flight and since > 0 else 0.0
            last_tick = float(getattr(unreal, "_mcp_last_tick_at", 0.0) or 0.0)
            current_command = str(metrics.get("last_command") or "") if (in_flight or dispatching) else ""
        tick_age = (now - last_tick) if last_tick > 0 else -1.0
        started = float(metrics.get("started_at") or 0.0)
        uptime = (now - started) if started > 0 else 0.0
        project = getattr(unreal, "_mcp_project_cache", None) or {}
        body = json.dumps({
            "status": "ok",
            "version": PROTOCOL_VERSION,
            "port": unreal._mcp_bound_port,
            "commands": handler_names(),
            # Busy = a command is on the main thread or queued. Agents must not
            # treat this as "wedged" — wedged is GET ok + tick_age stale while idle.
            "busy": in_flight or qsize > 0 or dispatching,
            "in_flight": in_flight,
            "dispatching": dispatching,
            "in_flight_age_sec": round(in_flight_age, 1),
            "queue_size": qsize,
            "tick_age_sec": round(tick_age, 2) if tick_age >= 0 else None,
            "uptime_sec": round(uptime, 1),
            "current_command": current_command,
            "project_name": str(project.get("project_name") or ""),
            "project_dir": str(project.get("project_dir") or ""),
            "content_root": str(project.get("content_root") or ""),
            "command_timings": list(metrics.get("command_timings") or [])[-20:],
        }).encode()
        self._send_json(200, body)

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

        done = threading.Event()

        # Atomic try-accept: check + set in_flight + enqueue under one lock so two
        # ThreadingHTTPServer workers cannot both pass a non-atomic busy check.
        with accept_lock:
            if unreal._mcp_in_flight or command_queue.qsize() > 0:
                self._send_json(503, _busy_payload())
                return
            unreal._mcp_request_counter += 1
            req_id = f"req_{unreal._mcp_request_counter}_{time.time_ns()}"
            with responses_lock:
                response_events[req_id] = done
            # Tick clears _mcp_in_flight when the command finishes. Never clear it
            # here on timeout — that let the next parallel MCP tool enqueue while the
            # editor was still running a heavy wire/spawn and froze UEFN.
            # Tick self-heals orphaned in_flight after STUCK_INFLIGHT_SEC when idle.
            unreal._mcp_in_flight = True
            unreal._mcp_in_flight_since = time.time()
            command_queue.put((req_id, command, params))

        if not done.wait(timeout=HTTP_TIMEOUT_SEC):
            with responses_lock:
                response_events.pop(req_id, None)
            self._send_json(504, json.dumps({"success": False, "error": f"Command '{command}' timed out"}).encode())
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
