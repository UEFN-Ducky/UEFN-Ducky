from __future__ import annotations

from backend.agent.model_fetch import ModelInfo
from backend.agent.video.limits import clamp, media_limits_for, request_image_max, video_limits
from frontend.settings import PanelSettings


def test_defaults_when_unset():
    lim = video_limits()
    assert lim.max_bytes == 100 * 1024 * 1024
    assert lim.frames_per_video == 20
    assert lim.max_images_per_message == 20  # Auto, provider-agnostic


def test_values_are_read_from_settings_and_clamped(monkeypatch):
    monkeypatch.setattr("backend.agent.model_fetch.get_model_info", lambda p, m: None)
    s = PanelSettings.load()
    s.video_max_mb = 999
    s.video_frames_per_video = 99
    s.max_images_per_message = 60
    s.save()
    lim = media_limits_for("openai", "m")
    assert lim.max_bytes == 200 * 1024 * 1024
    assert lim.frames_per_video == 40
    assert lim.max_images_per_message == 60


def test_clamp_bad_input_falls_back_to_default():
    assert clamp("nope", 1, 40, 20) == 20
    assert clamp(None, 1, 40, 20) == 20
    assert clamp("7", 1, 40, 20) == 7


def _info(monkeypatch, max_images):
    monkeypatch.setattr(
        "backend.agent.model_fetch.get_model_info", lambda p, m: ModelInfo(id=m, max_images=max_images)
    )


def test_request_image_max_table(monkeypatch):
    monkeypatch.setattr("backend.agent.model_fetch.get_model_info", lambda p, m: None)
    assert request_image_max("anthropic", "x") == 100
    assert request_image_max("openai", "x") == 500
    assert request_image_max("gemini", "x") == 3000
    assert request_image_max("mistral", "x") == 20
    assert request_image_max("", "") == 20


def test_request_image_max_model_override(monkeypatch):
    _info(monkeypatch, 8)
    assert request_image_max("openai", "m") == 8
    _info(monkeypatch, 0)
    assert request_image_max("openai", "m") == 500


def test_media_limits_auto_follow_provider(monkeypatch):
    monkeypatch.setattr("backend.agent.model_fetch.get_model_info", lambda p, m: None)
    lim = media_limits_for("openai", "m")
    assert (lim.frames_per_video, lim.max_images_per_message) == (20, 500)
    lim = media_limits_for("mistral", "m")
    assert (lim.frames_per_video, lim.max_images_per_message) == (20, 20)
    _info(monkeypatch, 8)
    lim = media_limits_for("openai", "m")
    assert (lim.frames_per_video, lim.max_images_per_message) == (8, 8)


def test_media_limits_manual_is_clamped_to_the_model_max(monkeypatch):
    monkeypatch.setattr("backend.agent.model_fetch.get_model_info", lambda p, m: None)
    s = PanelSettings.load()
    s.video_frames_per_video = 30
    s.max_images_per_message = 90
    s.save()
    lim = media_limits_for("mistral", "m")
    assert (lim.frames_per_video, lim.max_images_per_message) == (20, 20)


def test_media_limits_manual_wins(monkeypatch):
    monkeypatch.setattr("backend.agent.model_fetch.get_model_info", lambda p, m: None)
    s = PanelSettings.load()
    s.video_frames_per_video = 12
    s.max_images_per_message = 60
    s.save()
    lim = media_limits_for("openai", "m")
    assert (lim.frames_per_video, lim.max_images_per_message) == (12, 60)
