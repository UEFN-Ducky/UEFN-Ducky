"""Panel API: video uploads (staging), Settings → Videos, and the ffmpeg install."""

from __future__ import annotations

from typing import Any

import frontend.ui_web.panel_api as _pa


class PanelApiVideoMixin:
    def stage_video_attachment(
        self, conv_id: str = "", name: str = "", mime: str = "", data_base64: str = ""
    ) -> dict[str, Any]:
        """Save a dropped video so the chat shows it and the agent gets its path. Nothing else:
        no frames, transcript or ffmpeg until an agent asks (web_video on the path)."""
        from backend.agent.video import ffmpeg_install
        from backend.agent.video.staging import stage_video

        del conv_id  # every chat treats a video the same way now: referenced, not broken down
        try:
            staged = stage_video(str(name or ""), str(mime or ""), str(data_base64 or ""))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **staged, "needs_ffmpeg": False, "ffmpeg": ffmpeg_install.status(),
                "prep": dict(_READY_PREP)}

    def get_video_prep_status(self, staged_ids: list[str] | None = None) -> dict[str, Any]:
        from backend.agent.video import prep

        ids = [str(i) for i in (staged_ids or []) if str(i).strip()]
        return {"ok": True, "prep": {sid: prep.prep_status(sid) for sid in ids}}

    def retry_video_prep(self, staged_id: str = "") -> dict[str, Any]:
        from backend.agent.video import prep

        return {"ok": True, "prep": prep.retry_prep(str(staged_id or "").strip())}

    def get_video_settings(self, conv_id: str = "", model: str = "", coding_agent: str = "") -> dict[str, Any]:
        from backend.agent.video import ffmpeg_install
        from backend.agent.video import limits as L
        from backend.agent.video.limits import video_limits

        s = _pa.PanelSettings.load()
        lim = video_limits()
        return {
            "ok": True,
            "video_max_mb": lim.max_bytes // (1024 * 1024),
            "video_frames_per_video": L.clamp_auto(
                getattr(s, "video_frames_per_video", 0), *L.FRAMES_PER_VIDEO_RANGE, 0
            ),
            "max_images_per_message": L.clamp_auto(
                getattr(s, "max_images_per_message", 0), *L.MAX_IMAGES_PER_MESSAGE_RANGE, 0
            ),
            "auto": _auto_limits(str(conv_id or "").strip(), str(model or ""), str(coding_agent or "")),
            "ffmpeg": ffmpeg_install.status(),
        }

    def set_video_settings(self, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        from backend.agent.video import limits as L

        data = _pa._coerce_mapping(patch, label="video settings patch")
        s = _pa.PanelSettings.load()
        if "video_max_mb" in data:
            s.video_max_mb = L.clamp(data["video_max_mb"], *L.VIDEO_MAX_MB_RANGE, L.DEFAULT_VIDEO_MAX_MB)
        if "video_frames_per_video" in data:
            s.video_frames_per_video = L.clamp_auto(
                data["video_frames_per_video"], *L.FRAMES_PER_VIDEO_RANGE, 0
            )
        if "max_images_per_message" in data:
            s.max_images_per_message = L.clamp_auto(
                data["max_images_per_message"], *L.MAX_IMAGES_PER_MESSAGE_RANGE, 0
            )
        s.save()
        return self.get_video_settings()

    def get_ffmpeg_status(self) -> dict[str, Any]:
        from backend.agent.video import ffmpeg_install

        return ffmpeg_install.status()

    def install_ffmpeg(self) -> dict[str, Any]:
        from backend.agent.video import ffmpeg_install

        return ffmpeg_install.start_install()

    def remove_ffmpeg(self) -> dict[str, Any]:
        from backend.agent.video import ffmpeg_install

        return ffmpeg_install.remove()


_READY_PREP: dict[str, Any] = {
    "state": "ready", "frames_done": 0, "frames_total": 0,
    "transcript": "skipped", "transcript_note": "", "error": "", "sendable": True,
}


def _auto_limits(conv_id: str, model: str = "", coding_agent: str = "") -> dict[str, int]:
    """What Auto resolves to: the chat's provider/model when known, else provider-agnostic.

    ``model`` / ``coding_agent`` are the composer's current pick, which can differ from
    the saved chat until the next send; the file limit follows that model's context window.
    """
    from backend.agent.attachments import file_text_budget
    from backend.agent.video.limits import AUTO_FRAMES_CAP, request_image_max
    from frontend.ui_web.chats import conversations as chat_convs

    provider = saved_model = agent = ""
    conv = chat_convs.load_conversation(conv_id) if conv_id else None
    if conv is not None and not getattr(conv, "is_group", False):
        provider = str(getattr(conv, "provider", "") or "")
        saved_model = str(getattr(conv, "model", "") or "")
        agent = str(getattr(conv, "coding_agent", "") or "")
    model = (model or "").strip() or saved_model
    agent = (coding_agent or "").strip() or agent
    rmax = request_image_max(provider, model)
    return {
        "frames_per_video": min(AUTO_FRAMES_CAP, rmax),
        "max_images_per_message": rmax,
        **file_text_budget(provider, model, agent),
    }
