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
    assert out["auto"] == {"frames_per_video": 20, "max_images_per_message": 20, "context_tokens": 0, "file_max_tokens": 0, "file_inline_tokens": 0}
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
    assert api.get_video_settings("c1")["auto"] == {"frames_per_video": 20, "max_images_per_message": 100, "context_tokens": 0, "file_max_tokens": 0, "file_inline_tokens": 0}
    assert api.get_video_settings()["auto"] == {"frames_per_video": 20, "max_images_per_message": 20, "context_tokens": 0, "file_max_tokens": 0, "file_inline_tokens": 0}


def test_file_limit_follows_the_picked_model(monkeypatch):
    from types import SimpleNamespace

    from backend.agent.model_fetch import ModelInfo

    windows = {"claude-x": 200_000, "big-1": 1_000_000, "tools-1": 400_000}

    def info(p, m):
        return ModelInfo(id=m, context_limit=windows[m], supports_tools=m == "tools-1") if m in windows else None

    monkeypatch.setattr("backend.agent.video.ffmpeg_install.status", lambda: {"state": "missing"})
    monkeypatch.setattr("backend.agent.model_fetch.get_model_info", info)
    monkeypatch.setattr("backend.agent.model_capabilities.get_model_info", info)
    monkeypatch.setattr(
        "frontend.ui_web.project_chats.load_conversation",
        lambda cid, project_root=None: SimpleNamespace(is_group=False, provider="anthropic", model="claude-x"),
    )
    api = PanelApi()
    saved = api.get_video_settings("c1")["auto"]
    assert (saved["context_tokens"], saved["file_max_tokens"]) == (200_000, 150_000)
    # Switching models in the composer changes the limit before anything is sent.
    picked = api.get_video_settings("c1", "big-1")["auto"]
    assert (picked["context_tokens"], picked["file_max_tokens"]) == (1_000_000, 750_000)
    unknown = api.get_video_settings("c1", "mystery-model")["auto"]
    assert (unknown["context_tokens"], unknown["file_max_tokens"]) == (0, 0)
    # A model that can call tools reads a big file in parts: no cap, only a paste-whole share.
    tools = api.get_video_settings("c1", "tools-1")["auto"]
    assert (tools["file_max_tokens"], tools["file_inline_tokens"]) == (0, 8_000)
    coding = api.get_video_settings("c1", "big-1", "codex")["auto"]
    assert coding["file_max_tokens"] == 0


def test_pasted_file_text_is_capped_whatever_the_window():
    from backend.agent.attached_files import INLINE_MAX_TOKENS, inline_token_budget

    assert inline_token_budget(1_000_000) == INLINE_MAX_TOKENS == 8_000
    assert inline_token_budget(50_000) == 5_000
    assert inline_token_budget(0) == 0


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


def test_a_file_attached_to_an_answer_is_saved_in_that_chat(monkeypatch, tmp_path):
    import base64

    import frontend.ui_web.conversation_attachments as ca

    att = tmp_path / "c1" / "attachments"
    monkeypatch.setattr(ca, "chat_attachments_dir", lambda cid, create=False: att.mkdir(parents=True, exist_ok=True) or att)
    page = '<h1>Kit</h1><img src="data:image/png;base64,' + "A" * 600 + '">'
    out = PanelApi().save_chat_file("c1", "Island Kit.html", base64.b64encode(page.encode()).decode())
    assert out["ok"] and out["path"].endswith("_Island_Kit.html")
    assert (att / out["path"].split("\\")[-1].split("/")[-1]).read_text(encoding="utf-8") == page
    assert "A" * 600 not in open(out["readable_path"], encoding="utf-8").read()
    assert PanelApi().save_chat_file("c1", "x.bin", "not base64!!")["ok"] is False
