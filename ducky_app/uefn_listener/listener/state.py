"""Shared queues and metrics on ``unreal`` (survives script re-runs)."""

from __future__ import annotations

import queue
import threading
from typing import Any, Dict, List

import unreal


def _init_shared_state() -> None:
    defaults: Dict[str, Any] = {
        "_mcp_server": None,
        "_mcp_server_thread": None,
        "_mcp_tick_handle": None,
        "_mcp_bound_port": 0,
        "_mcp_command_queue": queue.Queue(),
        "_mcp_main_queue": queue.Queue(),
        "_mcp_responses": {},
        "_mcp_response_events": {},
        "_mcp_responses_lock": threading.Lock(),
        # Serializes POST accept (busy check + set in_flight + enqueue). Without this,
        # ThreadingHTTPServer lets two workers both see idle and both enqueue — freezes UEFN.
        "_mcp_accept_lock": threading.Lock(),
        "_mcp_request_counter": 0,
        "_mcp_in_flight": False,
        "_mcp_in_flight_since": 0.0,
        "_mcp_dispatching": False,
        # Slate post-tick heartbeat — GET /health reads this; never POST ping for status.
        "_mcp_last_tick_at": 0.0,
        # Cached project identity for GET health (refreshed on the game thread).
        "_mcp_project_cache": {
            "at": 0.0,
            "project_name": "",
            "project_dir": "",
            "content_root": "",
        },
        "_mcp_reload_in_progress": False,
        "_mcp_log_ring": [],
        "_mcp_metrics": {
            "started_at": 0.0,
            "total_requests": 0,
            "total_errors": 0,
            "last_request_at": 0.0,
            "last_command": "",
            "last_error": "",
            "last_client_ping": 0.0,
            "response_times_ms": [],
            # Per-command ring: [{"name": str, "ms": float, "ts": float}, ...]
            "command_timings": [],
        },
        "_mcp_status_window": None,
    }
    for attr, default in defaults.items():
        if not hasattr(unreal, attr):
            setattr(unreal, attr, default)


_init_shared_state()

command_queue: queue.Queue = unreal._mcp_command_queue
main_queue: queue.Queue = unreal._mcp_main_queue
responses: Dict[str, dict] = unreal._mcp_responses
response_events: Dict[str, threading.Event] = unreal._mcp_response_events
responses_lock: threading.Lock = unreal._mcp_responses_lock
accept_lock: threading.Lock = unreal._mcp_accept_lock
log_ring: List[str] = unreal._mcp_log_ring
metrics: Dict[str, Any] = unreal._mcp_metrics
