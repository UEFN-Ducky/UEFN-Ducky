from __future__ import annotations

import base64
import os
import time

import pytest

from backend.agent.video import staging
from backend.agent.video.frames import VideoError
from backend.agent.video.routing import gemini_inline_mime, needs_frames
from frontend.app_paths import resolve_app_data_dir

MB = 1024 * 1024


def test_gemini_inline_only_small_supported_types():
    assert gemini_inline_mime("video/mp4", 5 * MB) == "video/mp4"
    assert gemini_inline_mime("video/quicktime", 5 * MB) == "video/mov"
    assert gemini_inline_mime("video/x-matroska", 5 * MB) is None
    assert gemini_inline_mime("video/mp4", 21 * MB) is None


def test_needs_frames_rules():
    assert needs_frames("video/mp4", MB, provider="gemini", external=False) is False
    assert needs_frames("video/mp4", MB, provider="GEMINI", external=False) is False
    assert needs_frames("video/mp4", MB, provider="anthropic", external=False) is True
    assert needs_frames("video/mp4", MB, provider="gemini", external=True) is True
    assert needs_frames("video/mp4", 30 * MB, provider="gemini", external=False) is True


def test_normalize_mime_falls_back_to_extension():
    assert staging.normalize_video_mime("", "clip.MKV") == "video/x-matroska"
    assert staging.normalize_video_mime("video/mp4", "x") == "video/mp4"
    assert staging.normalize_video_mime("video/avi", "x.avi") is None


def test_stage_and_resolve_roundtrip():
    data = b"\x00" * 1024
    out = staging.stage_video("bug.mp4", "video/mp4", "data:video/mp4;base64," + base64.b64encode(data).decode())
    assert out["size_bytes"] == 1024 and out["mime"] == "video/mp4"
    path = staging.resolve_staged(out["staged_id"])
    assert path.read_bytes() == data and path.suffix == ".mp4"


def test_stage_rejects_unsupported_and_too_big(monkeypatch):
    with pytest.raises(VideoError, match="Unsupported video format"):
        staging.stage_video("a.avi", "video/avi", base64.b64encode(b"x").decode())
    from backend.agent.video import limits

    monkeypatch.setattr(staging, "video_limits", lambda: limits.VideoLimits(10, 20, 40))
    with pytest.raises(VideoError, match="exceeds the 0MB video limit|exceeds"):
        staging.stage_video("a.mp4", "video/mp4", base64.b64encode(b"x" * 11).decode())


def test_resolve_rejects_bad_ids():
    for bad in ["../x.mp4", "abc.mp4", "0" * 32 + ".exe", ""]:
        with pytest.raises(VideoError):
            staging.resolve_staged(bad)


def test_old_staged_files_are_swept():
    old = staging.staging_dir(create=True) / ("a" * 32 + ".mp4")
    old.write_bytes(b"x")
    past = time.time() - 2 * 24 * 3600
    os.utime(old, (past, past))
    staging.stage_video("b.mp4", "video/mp4", base64.b64encode(b"y").decode())
    assert not old.exists()


def test_resolve_staged_touches_mtime():
    """Staged files touched on access should not be swept."""
    data = b"\x00" * 1024
    out = staging.stage_video("old.mp4", "video/mp4", "data:video/mp4;base64," + base64.b64encode(data).decode())
    old_path = staging.resolve_staged(out["staged_id"])
    past = time.time() - 2 * 24 * 3600
    os.utime(old_path, (past, past))
    # Touch it via resolve_staged
    staging.resolve_staged(out["staged_id"])
    # Stage another video (which sweeps)
    staging.stage_video("new.mp4", "video/mp4", base64.b64encode(b"y").decode())
    # Old file should still exist
    assert old_path.exists()


def test_safe_media_path_in_staging_dir():
    """Accept a file in staging_dir() with video suffix."""
    sdir = staging.staging_dir(create=True)
    test_file = sdir / "test.mp4"
    test_file.write_bytes(b"x")
    result = staging.safe_media_path(test_file, suffixes={".mp4", ".webm", ".mov", ".mkv"})
    assert result == test_file.resolve()


def test_safe_media_path_in_chat_attachments():
    """Accept files in chats/*/conversations/*/attachments/ with allowed suffixes."""
    app_data = resolve_app_data_dir(for_write=True)
    attachments_dir = app_data / "chats" / "projects" / "p" / "conversations" / "c1" / "attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)

    # Test .mp4 with video suffixes
    mp4_file = attachments_dir / "1_0_v.mp4"
    mp4_file.write_bytes(b"video")
    result = staging.safe_media_path(mp4_file, suffixes={".mp4", ".webm", ".mov", ".mkv"})
    assert result == mp4_file.resolve()

    # Test .jpg with image suffixes
    jpg_file = attachments_dir / "2_0_i.jpg"
    jpg_file.write_bytes(b"image")
    result = staging.safe_media_path(jpg_file, suffixes={".jpg", ".png"})
    assert result == jpg_file.resolve()


def test_safe_media_path_rejects_panel_settings():
    """Reject files outside allowed paths, even with allowed suffix."""
    app_data = resolve_app_data_dir(for_write=True)
    settings_file = app_data / "panel_settings.json"
    settings_file.write_bytes(b"{}")
    result = staging.safe_media_path(settings_file, suffixes={".json"})
    assert result is None


def test_safe_media_path_rejects_wrong_suffix_in_attachments():
    """Reject files with disallowed suffixes in attachments folder."""
    app_data = resolve_app_data_dir(for_write=True)
    attachments_dir = app_data / "chats" / "projects" / "p" / "conversations" / "c2" / "attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)

    json_file = attachments_dir / "data.json"
    json_file.write_bytes(b"{}")
    result = staging.safe_media_path(json_file, suffixes={".mp4", ".webm", ".mov", ".mkv"})
    assert result is None


def test_safe_media_path_rejects_outside_app_data(tmp_path):
    """Reject paths outside AppData."""
    outside = tmp_path.parent / "elsewhere.mp4"
    outside.write_bytes(b"x")
    result = staging.safe_media_path(outside, suffixes={".mp4"})
    assert result is None


def test_safe_media_path_rejects_nonexistent():
    """Reject non-existent paths."""
    nonexistent = staging.staging_dir() / "does_not_exist.mp4"
    result = staging.safe_media_path(nonexistent, suffixes={".mp4"})
    assert result is None


def test_safe_media_path_rejects_traversal():
    """Reject traversal attempts like attachments/../settings.json."""
    app_data = resolve_app_data_dir(for_write=True)
    settings_file = app_data / "panel_settings.json"
    settings_file.write_bytes(b"{}")

    attachments_dir = app_data / "chats" / "projects" / "p" / "conversations" / "c3" / "attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)

    # Traversal path that resolves to settings file
    traversal = str(attachments_dir / "../../../panel_settings.json")
    result = staging.safe_media_path(traversal, suffixes={".json"})
    assert result is None
