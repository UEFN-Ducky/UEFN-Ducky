"""Broker access from tools: only the window owns queues and turn lifecycle."""
from __future__ import annotations

import json
import urllib.request


def _call(operation: str, *args, **kwargs):
    from frontend.ui_web.agent_modes import get_panel_push

    if get_panel_push() is not None:
        from backend.agent import a2a_broker

        return getattr(a2a_broker, operation)(*args, **kwargs)
    from frontend.settings import PANEL_LISTENER_PORT

    request = urllib.request.Request(
        f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_api/agent_broker_call",
        data=json.dumps({"args": {"operation": operation, "args": list(args), "kwargs": kwargs}}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Ducky window broker unavailable; message was not queued locally") from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError("Ducky window broker request failed; message was not queued locally")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Invalid Ducky window broker response")
    if not result.get("ok"):
        raise RuntimeError(str(result.get("error", "Ducky window broker request failed")))
    return result.get("value")


def send(**kwargs):
    return _call("send", **kwargs)


def read_inbox(conv_id):
    return _call("read_inbox", conv_id)


def send_notice(**kwargs):
    return _call("send_notice", **kwargs)


def open_thread(*args, **kwargs):
    return _call("open_thread", *args, **kwargs)


def close_thread(response_id):
    return _call("close_thread", response_id)


def open_threads_for_receiver(conv_id):
    return _call("open_threads_for_receiver", conv_id)


def stats():
    return _call("stats")


def on_agent_stopped(*args, **kwargs):
    return _call("on_agent_stopped", *args, **kwargs)


def on_agent_cancelled_by_user(conv_id):
    return _call("on_agent_cancelled_by_user", conv_id)


def acknowledge_reports(coordinator_id, member_id):
    return _call("acknowledge_reports", coordinator_id, member_id)


def acknowledge_plan_changes(previous, plan, actor_id, project_root=None):
    if not actor_id or actor_id != plan.get("chat_id") or previous.get("nodes") == plan.get("nodes"):
        return None
    return _call("acknowledge_plan_changes", previous, plan, actor_id, project_root)


def acknowledge_saved_plan(previous, plan, project_root=None):
    from backend.workspace.identity import resolve_context

    context = resolve_context()
    if context is not None and context.conv_id == plan.get("chat_id"):
        # Saving the plan has already succeeded. A lost acknowledgement leaves
        # the report pending; it must not turn that save into a reported failure.
        try:
            acknowledge_plan_changes(previous, plan, context.conv_id, project_root)
        except RuntimeError:
            pass
