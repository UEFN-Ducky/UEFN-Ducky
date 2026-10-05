from __future__ import annotations

import base64

import pytest

from backend.agent.attachments import parse_attachment_dict, parse_attachment_dicts
from backend.agent.multimodal_content import media_attachments
from backend.agent.video import staging
from frontend.ui_web.conversation_attachments import (
    hydrate_attachment_dict,
    persist_message_attachments,
)

_PNG = base64.b64encode(b"\x89PNG\r\n").decode()


def _staged(data: bytes = b"vid", name: str = "bug.mp4") -> dict:
    out = staging.stage_video(name, "video/mp4", base64.b64encode(data).decode())
    return {"kind": "video", "name": name, "mime": "video/mp4", "staged_id": out["staged_id"]}


def test_parse_staged_video():
    att = parse_attachment_dict(_staged())
    assert att.kind == "video" and att.size_bytes == 3
    assert att.file_path.endswith(".mp4")
    assert media_attachments([att]) == [att]


def test_parse_rejects_path_outside_app_data(tmp_path):
    outside = tmp_path.parent / "secret.mp4"
    outside.write_bytes(b"x")
    assert parse_attachment_dict({"kind": "video", "name": "s.mp4", "abs_path": str(outside)}) is None


def test_expired_stage_is_an_error():
    with pytest.raises(ValueError, match="expired"):
        parse_attachment_dict({"kind": "video", "name": "x.mp4", "staged_id": "f" * 32 + ".mp4"})


def test_image_cap_comes_from_settings():
    from frontend.settings import PanelSettings

    s = PanelSettings.load()
    s.max_images_per_message = 2
    s.save()
    imgs = [{"kind": "image", "name": f"{i}.png", "data_base64": _PNG} for i in range(3)]
    with pytest.raises(ValueError, match="At most 2 images"):
        parse_attachment_dicts(imgs, current=True)


def test_persist_copies_video_and_hydrate_never_inlines_it(tmp_path):
    att = parse_attachment_dict(_staged(b"0123456789", "my clip.mp4"))
    rows = persist_message_attachments("conv1", 1.5, [att], tmp_path)
    assert rows == [{
        "kind": "video", "name": "my clip.mp4", "mime": "video/mp4",
        "path": "attachments/1500_0_my_clip.mp4", "size_bytes": 10,
    }]
    stored = tmp_path / "conv1" / rows[0]["path"]
    assert stored.read_bytes() == b"0123456789"
    frame = stored.with_name(stored.name + ".f01-01.jpg")
    frame.write_bytes(b"jpg")
    row = {**rows[0], "frames": [{"path": f"attachments/{frame.name}", "t_s": 0.5}]}
    hyd = hydrate_attachment_dict(row, "conv1", tmp_path)
    assert "data_base64" not in hyd
    assert hyd["abs_path"] == str(stored)
    assert hyd["media_url"].endswith("/chat-attachments/conv1/1500_0_my_clip.mp4")
    assert hyd["frames"][0]["abs_path"] == str(frame)
    assert hyd["frames"][0]["media_url"].endswith(f"/chat-attachments/conv1/{frame.name}")


def test_persist_adds_extension_when_name_has_none(tmp_path):
    raw = _staged(b"x", "blob")
    raw["name"] = "blob"
    att = parse_attachment_dict(raw)
    rows = persist_message_attachments("c", 1.0, [att], tmp_path)
    assert rows[0]["path"].endswith("_blob.mp4")


def _app_data():
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir(for_write=True)


def test_parse_rejects_non_video_file_inside_app_data():
    settings = _app_data() / "panel_settings.json"
    settings.write_text("{}", encoding="utf-8")
    assert parse_attachment_dict({"kind": "video", "name": "x.mp4", "abs_path": str(settings)}) is None


def test_parse_rejects_mp4_in_unrelated_app_data_subdir():
    other = _app_data() / "other"
    other.mkdir(parents=True, exist_ok=True)
    f = other / "x.mp4"
    f.write_bytes(b"x")
    assert parse_attachment_dict({"kind": "video", "name": "x.mp4", "abs_path": str(f)}) is None


def test_parse_accepts_chat_attachment_and_filters_frames(tmp_path):
    att_dir = _app_data() / "chats" / "projects" / "p" / "conversations" / "c1" / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    video = att_dir / "1_0_clip.mp4"
    video.write_bytes(b"vid")
    good = att_dir / "1_0_clip.mp4.f01-01.jpg"
    good.write_bytes(b"jpg")
    wrong_suffix = att_dir / "frame.png"
    wrong_suffix.write_bytes(b"png")
    outside = tmp_path / "out.jpg"
    outside.write_bytes(b"jpg")
    att = parse_attachment_dict({
        "kind": "video", "name": "clip.mp4", "abs_path": str(video),
        "frames": [
            {"abs_path": str(good), "t_s": 0.5},
            {"abs_path": str(wrong_suffix), "t_s": 1.0},
            {"abs_path": str(outside), "t_s": 2.0},
        ],
    })
    assert att is not None and att.file_path == str(video.resolve())
    assert att.frames == [(str(good.resolve()), 0.5)]


def test_current_video_that_cannot_parse_raises():
    row = {"kind": "video", "name": "clip.mp4", "mime": "video/mp4"}
    assert parse_attachment_dicts([row]) == []
    with pytest.raises(ValueError, match="no longer available"):
        parse_attachment_dicts([row], current=True)


def test_persist_video_copy_failure_raises(tmp_path, monkeypatch):
    att = parse_attachment_dict(_staged(b"0123", "a.mp4"))

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("frontend.ui_web.conversation_attachments.shutil.copyfile", boom)
    with pytest.raises(ValueError, match="Could not save video 'a.mp4'"):
        persist_message_attachments("conv1", 1.5, [att], tmp_path)


def test_accented_filename_is_servable(tmp_path):
    from frontend.ui_web.conversation_attachments import _CHAT_FILE_RE
    from frontend.ui_web.panel_httpd import _CHAT_ATTACHMENT_RE

    name = "Enregistrement d’écran 2026-10-05 101112.mp4"
    att = parse_attachment_dict(_staged(b"0123", name))
    rows = persist_message_attachments("conv1", 1.5, [att], tmp_path)
    fname = rows[0]["path"].split("/", 1)[1]
    assert _CHAT_FILE_RE.fullmatch(fname) and fname.endswith(".mp4")
    assert _CHAT_ATTACHMENT_RE.fullmatch(f"chat-attachments/conv1/{fname}")
    assert rows[0]["name"] == name


def test_history_parsing_ignores_lowered_limits(tmp_path):
    from backend.agent.attachments import attachments_from_message_dict
    from frontend.settings import PanelSettings

    s = PanelSettings.load()
    s.max_images_per_message = 2
    s.save()
    msg = {"role": "user", "attachments": [{"kind": "image", "name": f"{i}.png", "data_base64": _PNG} for i in range(3)]}
    assert len(attachments_from_message_dict(msg)) == 3
    with pytest.raises(ValueError, match="At most 2 images"):
        parse_attachment_dicts(msg["attachments"], current=True)


def test_history_video_over_current_size_limit_still_parses(monkeypatch):
    from backend.agent.video import limits

    row = _staged(b"0123456789")
    monkeypatch.setattr(limits, "video_limits", lambda: limits.VideoLimits(max_bytes=4, frames_per_video=20, max_images_per_message=40))
    assert parse_attachment_dict(row).size_bytes == 10
    with pytest.raises(ValueError, match="video limit"):
        parse_attachment_dict(row, current=True)


def test_backfill_is_skipped_for_external_agents(monkeypatch):
    from types import SimpleNamespace

    from frontend.ui_web import agent_modes

    def boom(*_a, **_k):
        raise AssertionError("backfill must not run for external agents")

    monkeypatch.setattr("backend.agent.video.send.backfill_history_frames", boom)
    conv = SimpleNamespace(messages=[{"role": "user", "attachments": [{"kind": "video", "name": "a.mp4"}]}])
    agent_modes._backfill_video_frames(conv, "c1", "anthropic", True, lambda _e: None, None)
    with pytest.raises(AssertionError):
        agent_modes._backfill_video_frames(conv, "c1", "anthropic", False, lambda _e: None, None)


def test_transcript_round_trips_through_parse_persist_hydrate(tmp_path):
    raw = dict(_staged(b"0123456789", "clip.mp4"), transcript="hello")
    att = parse_attachment_dict(raw)
    assert att.transcript == "hello"
    rows = persist_message_attachments("conv1", 2.5, [att], tmp_path)
    assert rows[0]["transcript"] == "hello"
    row = {**rows[0], "transcript_note": ""}
    hyd = hydrate_attachment_dict(row, "conv1", tmp_path)
    assert hyd["transcript"] == "hello" and hyd["transcript_note"] == ""


def test_transcript_capped_at_200k(tmp_path):
    att = parse_attachment_dict(dict(_staged(b"0123456789", "clip.mp4"), transcript="x" * 300_000))
    assert len(att.transcript) == 200_000
