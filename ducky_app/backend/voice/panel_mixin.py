"""PanelApi methods for the live voice lane (quick replies + its settings)."""

from __future__ import annotations

from typing import Any

_LANE_KEYS = ("voice_live_model", "voice_reply_length")


def _recent_rows(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for row in rows or []:
        role = str(row.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        text = str(row.get("text") or row.get("content") or "").strip()
        if text:
            out.append((role, text))
    return out


class PanelApiVoiceLaneMixin:
    def voice_lane_settings(self) -> dict[str, Any]:
        from frontend.settings import PanelSettings

        s = PanelSettings.load()
        return {
            "voice_live_model": str(getattr(s, "voice_live_model", "") or ""),
            "voice_reply_length": str(getattr(s, "voice_reply_length", "short") or "short"),
        }

    def save_voice_lane_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        from frontend.settings import PanelSettings

        data = {k: v for k, v in dict(patch or {}).items() if k in _LANE_KEYS}
        if not data:
            return self.voice_lane_settings()
        s = PanelSettings.load()
        if "voice_live_model" in data:
            s.voice_live_model = str(data["voice_live_model"] or "").strip()
        if "voice_reply_length" in data:
            s.voice_reply_length = str(data["voice_reply_length"] or "short").strip().lower()
        s.validate()
        s.save()
        try:
            from frontend.ui_web.agent_turns.push import push_ui_event

            push_ui_event({"type": "settings_changed", "keys": list(data)})
        except Exception:
            pass
        return self.voice_lane_settings()

    def voice_lane_turn(
        self,
        conv_id: str,
        text: str,
        work_status: str = "",
        agent_running: bool = False,
        model: str = "",
    ) -> dict[str, Any]:
        """Quick spoken answer for one live turn, or a one-line hand-off to the chat's agent."""
        from frontend.settings import PanelSettings

        from backend.voice.lane import run_voice_lane

        settings = PanelSettings.load()
        cid = str(conv_id or "").strip()
        summary = ""
        chat_model = ""
        recent: list[tuple[str, str]] = []
        if cid:
            try:
                from frontend.ui_web.chats.conversations import load_conversation

                conv = load_conversation(cid)
                if conv is not None:
                    summary = str(getattr(conv, "context_summary", "") or "")
                    chat_model = str(getattr(conv, "model", "") or "")
                load = getattr(self, "load_messages", None)
                if callable(load):
                    recent = _recent_rows(load(cid, None, 8))
            except Exception:
                pass
        pick = (
            str(model or "").strip()
            or str(getattr(settings, "voice_live_model", "") or "").strip()
            or chat_model
        )
        return run_voice_lane(
            str(text or ""),
            model=pick,
            reply_length=str(getattr(settings, "voice_reply_length", "short") or "short"),
            summary=summary,
            recent=recent,
            work_status=str(work_status or ""),
            agent_running=bool(agent_running),
        )
