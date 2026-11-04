"""UI push for agent turns: the panel's push handler, the active chat, and forwarding
events and runs from the stdio MCP bridge to the panel process."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

from frontend.settings import PANEL_LISTENER_PORT

PushFn = Callable[[dict[str, Any]], None]

_panel_push: PushFn | None = None
_active_conv_id = threading.local()


def set_panel_push(push: PushFn | None) -> None:
    """Register UI push handler so MCP chat tools update the React panel."""
    global _panel_push
    _panel_push = push


def get_panel_push() -> PushFn | None:
    return _panel_push


def get_active_conv_id() -> str | None:
    return getattr(_active_conv_id, "conv_id", None)


def _set_active_conv_id(conv_id: str | None) -> None:
    _active_conv_id.conv_id = conv_id


# --- Cross-process push forwarding (bridge process → panel process) ---------
# ducky_spawn_chat / ducky_send_chat_message run inside the stdio MCP bridge,
# a DIFFERENT process from the panel UI, where _panel_push is never set. Without
# this, notify_chats_changed and the sub-agent's stream events go to a no-op, so
# spawned chats/agents run invisibly (created on disk but never shown live). We
# forward every event to the panel's loopback HTTP server, which replays it onto
# its own push pipeline. A single daemon sender coalesces bursts into one POST so
# high-frequency stream deltas don't block the agent thread.
import queue as _queue
import urllib.request as _urlreq

_forward_queue: _queue.Queue[dict[str, Any]] = _queue.Queue(maxsize=4000)
_forward_started = False
_forward_lock = threading.Lock()


def _panel_event_url() -> str:
    return f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_event"


def _forward_sender() -> None:
    url = _panel_event_url()
    while True:
        batch = [_forward_queue.get()]
        try:
            while len(batch) < 250:
                batch.append(_forward_queue.get_nowait())
        except _queue.Empty:
            pass
        try:
            data = json.dumps(batch, ensure_ascii=False).encode("utf-8")
            req = _urlreq.Request(
                url, data=data, headers={"Content-Type": "application/json"}, method="POST"
            )
            _urlreq.urlopen(req, timeout=2.0).close()
        except Exception as _exc:
            # Panel closed / not yet listening — drop this batch silently.
            pass


def _ensure_forwarder() -> None:
    global _forward_started
    with _forward_lock:
        if _forward_started:
            return
        _forward_started = True
    threading.Thread(target=_forward_sender, name="bridge-panel-forward", daemon=True).start()


def _forward_to_panel(event: dict[str, Any]) -> None:
    _ensure_forwarder()
    try:
        _forward_queue.put_nowait(dict(event))
    except _queue.Full:
        pass


def _resolve_push(push: PushFn | None) -> PushFn:
    if push is not None:
        return push
    if _panel_push is not None:
        return _panel_push
    # No local panel (we're in the stdio bridge process) — forward to the panel.
    return _forward_to_panel


def push_ui_event(event: dict[str, Any]) -> None:
    """Send a one-off UI event to the panel from any process.

    Routes to the local panel push when running inside the panel process, and
    forwards over loopback HTTP when running in the stdio MCP bridge — so
    settings/plugin change notifications from UI tools reach React either way.
    """
    _resolve_push(None)(event)


# --- Run delegation (bridge process → panel process) ------------------------
# A run started from the stdio MCP bridge (ducky_spawn_chat / ducky_send_chat_
# message called by an external coding agent like Cursor) would otherwise
# execute the agent loop inside the bridge process, invisible to the panel's
# session registry, running-agent dots, streaming, and reconcile safety net. We
# instead ask the panel to run it, so the spawned duck is a first-class panel
# session. When no panel is reachable we fall back to running locally so spawns
# still work headless.
def _in_bridge_process() -> bool:
    return _panel_push is None


def _post_panel_run(payload: dict[str, Any], *, http_timeout: float | None) -> dict[str, Any] | None:
    """POST a run request to the panel.

    Returns the panel's JSON outcome on success; ``{"_delegation_timeout": True}``
    if the panel accepted the connection but didn't answer in time (it is running
    the turn — callers must NOT re-run locally or they'd duplicate it); ``None``
    only when the panel is unreachable (no panel open → safe to run locally).
    """
    import socket

    url = f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_run"
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = _urlreq.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with _urlreq.urlopen(req, timeout=http_timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}
    except (TimeoutError, socket.timeout):
        return {"_delegation_timeout": True}
    except Exception as _exc:
        return None


def notify_chats_changed(
    conv_id: str = "",
    title: str = "",
    folder_id: str = "",
    *,
    push: PushFn | None = None,
    open_tab: bool = True,
    removed_conv_ids: list[str] | None = None,
    removed_folder_ids: list[str] | None = None,
) -> None:
    """Tell the React panel to reload the sidebar (new chat/folder from MCP tools).

    Deleted or archived chats and deleted folders are named, so every open window
    drops those rows at once instead of showing them until its next reload.
    """
    event: dict[str, Any] = {"type": "chats_changed"}
    if conv_id:
        event["conv_id"] = conv_id
        event["title"] = title
        event["folder_id"] = folder_id
    if not open_tab:
        event["open"] = False
    if removed_conv_ids:
        event["removed_conv_ids"] = [str(cid) for cid in removed_conv_ids if cid]
    if removed_folder_ids:
        event["removed_folder_ids"] = [str(fid) for fid in removed_folder_ids if fid]
    _resolve_push(push)(event)


def notify_context_changed(conv_id: str, *, push: PushFn | None = None) -> None:
    """Tell the React panel to reload messages/context for a chat after context reset."""
    _resolve_push(push)({"type": "context_changed", "conv_id": conv_id})
