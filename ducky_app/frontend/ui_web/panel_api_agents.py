"""Window-owned agent broker RPC. No external process may keep broker state."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass


class PanelApiAgentsMixin:
    def agent_broker_call(self, operation: str, args=None, kwargs=None) -> dict:
        from backend.agent import a2a_broker

        allowed = {
            "send", "read_inbox", "send_notice", "open_thread", "close_thread",
            "open_threads_for_receiver", "stats", "on_agent_stopped",
            "on_agent_cancelled_by_user", "acknowledge_reports", "acknowledge_plan_changes",
        }
        if operation not in allowed:
            return {"ok": False, "error": "Unknown broker operation"}
        try:
            value = getattr(a2a_broker, operation)(*(args or []), **(kwargs or {}))
            if is_dataclass(value):
                value = asdict(value)
            elif isinstance(value, list):
                value = [asdict(item) if is_dataclass(item) else item for item in value]
            return {"ok": True, "value": value}
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}

    def agent_send(self, sender: str, to: str, message: str, expect_reply: bool = False, response_id: str = "") -> dict:
        return self.agent_broker_call("send", kwargs={"sender_conv_id": sender, "receiver_conv_id": to,
            "body": message, "expect_reply": expect_reply, "response_id": response_id})

    def agent_inbox(self, conv_id: str) -> dict:
        return self.agent_broker_call("read_inbox", args=[conv_id])

    def agent_send_notice(self, sender: str, to: str, body: str) -> dict:
        return self.agent_broker_call("send_notice", kwargs={"sender_conv_id": sender,
            "receiver_conv_id": to, "body": body})
