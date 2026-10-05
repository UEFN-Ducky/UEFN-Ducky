from __future__ import annotations

import base64

from frontend.ui_web.panel_api import PanelApi


def test_video_settings_roundtrip_and_clamp(monkeypatch):
    monkeypatch.setattr("backend.agent.video.ffmpeg_install.status", lambda: {"state": "missing"})
    api = PanelApi()
    out = api.set_video_settings({"video_max_mb": 500, "video_frames_per_video": "12", "max_images_per_message": 0})
    assert (out["video_max_mb"], out["video_frames_per_video"], out["max_images_per_message"]) == (200, 12, 0)
    assert api.get_video_settings()["video_frames_per_video"] == 12
    assert out["ffmpeg"] == {"state": "missing"}


def test_stage_unknown_conv_starts_ffmpeg(monkeypatch):
    started, preps = [], []
    monkeypatch.setattr("backend.agent.video.ffmpeg_install.start_install", lambda: started.append(1) or {"state": "installing"})
    monkeypatch.setattr(
        "backend.agent.video.prep.start_prep",
        lambda sid, *, frames, transcribe: preps.append((sid, frames, transcribe)) or {"state": "queued"},
    )
    res = PanelApi().stage_video_attachment("nope", "a.mp4", "video/mp4", base64.b64encode(b"x").decode())
    assert res["ok"] and res["needs_ffmpeg"] and started == [1]
    assert res["ffmpeg"] == {"state": "installing"}
    assert res["prep"] == {"state": "queued"}
    assert preps == [(res["staged_id"], 20, True)]


def test_stage_gemini_conv_skips_ffmpeg(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "frontend.ui_web.project_chats.load_conversation",
        lambda cid, project_root=None: SimpleNamespace(is_group=False, coding_agent="ducky", provider="gemini"),
    )
    monkeypatch.setattr("backend.agent.video.ffmpeg_install.start_install", lambda: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr("backend.agent.video.ffmpeg_install.status", lambda: {"state": "missing"})
    monkeypatch.setattr("backend.agent.video.prep.start_prep", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    res = PanelApi().stage_video_attachment("c1", "a.mp4", "video/mp4", base64.b64encode(b"x").decode())
    assert res["ok"] and res["needs_ffmpeg"] is False
    assert res["prep"]["state"] == "ready" and res["prep"]["transcript"] == "skipped"


def test_stage_error_is_returned_not_raised():
    res = PanelApi().stage_video_attachment("c", "a.avi", "video/avi", "eA==")
    assert res == {"ok": False, "error": "Unsupported video format for 'a.avi' — use MP4, WebM, MOV or MKV."}


def test_video_settings_accept_auto(monkeypatch):
    monkeypatch.setattr("backend.agent.video.ffmpeg_install.status", lambda: {"state": "missing"})
    api = PanelApi()
    api.set_video_settings({"video_frames_per_video": 12, "max_images_per_message": 60})
    out = api.set_video_settings({"video_frames_per_video": 0, "max_images_per_message": 0})
    assert out["video_frames_per_video"] == 0 and out["max_images_per_message"] == 0
    assert out["auto"] == {"frames_per_video": 20, "max_images_per_message": 20}
    assert api.get_video_settings()["video_frames_per_video"] == 0


def test_auto_limits_follow_the_conversation(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("backend.agent.video.ffmpeg_install.status", lambda: {"state": "missing"})
    monkeypatch.setattr("backend.agent.model_fetch.get_model_info", lambda p, m: None)
    monkeypatch.setattr(
        "frontend.ui_web.project_chats.load_conversation",
        lambda cid, project_root=None: SimpleNamespace(is_group=False, provider="anthropic", model="claude-x"),
    )
    api = PanelApi()
    assert api.get_video_settings("c1")["auto"] == {"frames_per_video": 20, "max_images_per_message": 100}
    assert api.get_video_settings()["auto"] == {"frames_per_video": 20, "max_images_per_message": 20}


def test_stage_prep_uses_model_frames(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("backend.agent.video.ffmpeg_install.start_install", lambda: {"state": "ready"})
    monkeypatch.setattr("backend.agent.model_fetch.get_model_info", lambda p, m: None)
    monkeypatch.setattr(
        "frontend.ui_web.project_chats.load_conversation",
        lambda cid, project_root=None: SimpleNamespace(
            is_group=False, coding_agent="ducky", provider="mistral", model="m"
        ),
    )
    got = []
    monkeypatch.setattr(
        "backend.agent.video.prep.start_prep",
        lambda sid, *, frames, transcribe: got.append(frames) or {"state": "queued"},
    )
    PanelApi().set_video_settings({"video_frames_per_video": 0})
    PanelApi().stage_video_attachment("c1", "a.mp4", "video/mp4", base64.b64encode(b"x").decode())
    assert got == [20]  # Auto: min(20, request max 20 for unknown provider)


def test_video_prep_status_and_retry(monkeypatch):
    monkeypatch.setattr("backend.agent.video.prep.prep_status", lambda sid: {"state": "extracting", "id": sid})
    monkeypatch.setattr("backend.agent.video.prep.retry_prep", lambda sid: {"state": "queued", "id": sid})
    api = PanelApi()
    assert api.get_video_prep_status(["a.mp4", "b.mp4"])["prep"] == {
        "a.mp4": {"state": "extracting", "id": "a.mp4"},
        "b.mp4": {"state": "extracting", "id": "b.mp4"},
    }
    assert api.get_video_prep_status() == {"ok": True, "prep": {}}
    assert api.retry_video_prep("a.mp4") == {"ok": True, "prep": {"state": "queued", "id": "a.mp4"}}
