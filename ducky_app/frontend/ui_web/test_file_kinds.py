"""Classification / mime coverage for the new audio & video file kinds."""

from __future__ import annotations

from frontend.ui_web.file_kinds import (
    classify_by_name,
    is_audio_file_name,
    is_video_file_name,
    mime_for_path,
)


def test_audio_suffixes_classify_as_audio():
    for name in ("song.mp3", "voice.WAV", "track.ogg", "clip.m4a", "sfx.aac", "master.flac"):
        assert classify_by_name(name) == "audio", name
        assert is_audio_file_name(name) is True


def test_video_suffixes_classify_as_video():
    for name in ("intro.mp4", "cutscene.WEBM", "clip.mov", "old.m4v", "theora.ogv"):
        assert classify_by_name(name) == "video", name
        assert is_video_file_name(name) is True


def test_audio_video_no_longer_classify_as_generic_binary():
    """They're still non-text/opaque suffixes, but now get their own audio/video kind
    (routed to the player panes) rather than falling into the generic hex-preview pane."""
    for name in ("song.mp3", "track.ogg", "clip.wav", "intro.mp4", "cutscene.webm"):
        assert classify_by_name(name) not in {"binary", "text"}, name


def test_mime_for_audio_and_video():
    # mimetypes.guess_type wins when the platform DB knows the suffix, so pin only
    # the ones our explicit fallback guarantees and otherwise assert the broad type.
    assert mime_for_path("song.mp3") == "audio/mpeg"
    assert mime_for_path("intro.mp4") == "video/mp4"
    assert mime_for_path("cutscene.webm") == "video/webm"
    assert mime_for_path("clip.mov") == "video/quicktime"
    for name in ("voice.m4a", "sfx.aac", "track.flac"):
        assert mime_for_path(name).startswith("audio/"), name
    for name in ("old.m4v", "theora.ogv"):
        assert mime_for_path(name).startswith("video/"), name


def test_ext_encoded_and_ws_encoded_audio_video_paths_classify():
    assert classify_by_name("ext:C:/tmp/song.mp3") == "audio"
    assert classify_by_name("ws:0/Content/movie.mp4") == "video"
