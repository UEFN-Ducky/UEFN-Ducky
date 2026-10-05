from __future__ import annotations

from backend.agent.message_attachment import MessageAttachment
from backend.agent.video.budget import (
    GEMINI_INLINE_BUDGET,
    apply_media_budget,
)

MB = 1024 * 1024


def _vid(name, *, size=1 * MB, frames=0, mime="video/mp4"):
    return MessageAttachment(
        kind="video", name=name, mime=mime, file_path=f"/x/{name}", size_bytes=size,
        frames=[(f"/x/{name}.{k}.jpg", float(k)) for k in range(frames)],
    )


def test_gemini_newest_video_inline_older_falls_back_to_frames():
    old, new = _vid("old.mp4", size=12 * MB, frames=20), _vid("new.mp4", size=12 * MB, frames=20)
    apply_media_budget([[old], [new]], provider="gemini")
    assert new.inline_ok and not new.omitted
    assert not old.inline_ok and not old.omitted
    assert 12 * MB < GEMINI_INLINE_BUDGET < 24 * MB


def test_gemini_older_without_frames_is_omitted():
    old, new = _vid("old.mp4", size=12 * MB), _vid("new.mp4", size=12 * MB)
    apply_media_budget([[old], [new]], provider="gemini")
    assert not old.inline_ok and old.omitted


def test_non_gemini_budget_keeps_newest_five_videos_of_twenty_frames():
    vids = [_vid(f"v{i}.mp4", frames=20) for i in range(6)]
    apply_media_budget([[v] for v in vids], provider="anthropic")
    assert [v.omitted for v in vids] == [True, False, False, False, False, False]
    assert all(not v.inline_ok for v in vids)


def test_images_are_never_altered_but_count_toward_the_budget():
    img = MessageAttachment(kind="image", name="a.png", mime="image/png", data_base64="aGk=")
    vid = _vid("v.mp4", frames=20)
    imgs = [MessageAttachment(kind="image", name=f"{i}.png", data_base64="aGk=") for i in range(90)]
    apply_media_budget([[vid], imgs, [img]], provider="anthropic")
    assert vid.omitted
    assert all(i.inline_ok and not i.omitted for i in imgs + [img])


def test_current_message_claims_budget_first():
    history = [_vid(f"h{i}.mp4", frames=20) for i in range(5)]
    current = _vid("cur.mp4", frames=20)
    apply_media_budget([[v] for v in history] + [[current]], provider="anthropic")
    assert not current.omitted
    assert history[0].omitted and not history[1].omitted


def test_non_inlinable_gemini_video_uses_frames():
    mkv = _vid("a.mkv", mime="video/x-matroska", frames=4)
    apply_media_budget([[mkv]], provider="gemini")
    assert not mkv.inline_ok and not mkv.omitted


def test_model_image_max_limits_budget(monkeypatch):
    from backend.agent.model_fetch import ModelInfo

    monkeypatch.setattr(
        "backend.agent.model_fetch.get_model_info", lambda p, m: ModelInfo(id=m, max_images=5)
    )
    old, new = _vid("old.mp4", frames=4), _vid("new.mp4", frames=4)
    apply_media_budget([[old], [new]], provider="openai", model="small")
    assert not new.omitted and old.omitted


def test_gemini_model_without_video_support_never_inlines(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "backend.agent.model_fetch.get_model_info", lambda p, m: SimpleNamespace(supports_video=False)
    )
    v = _vid("v.mp4", frames=4)
    apply_media_budget([[v]], provider="gemini", model="gemini-x")
    assert v.inline_ok is False and not v.omitted
