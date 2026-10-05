from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.agent.video import send
from backend.agent.video.frames import Frame, VideoError


def _setup(tmp_path, monkeypatch):
    conv_dir = tmp_path / "conv1"
    (conv_dir / "attachments").mkdir(parents=True)
    video = conv_dir / "attachments" / "1_0_bug.mp4"
    video.write_bytes(b"v")
    calls = []

    def fake_extract(path, n):
        calls.append((path, n))
        out = []
        for k in range(n):
            p = path.with_name(f"{path.name}.f{n:02d}-{k + 1:02d}.jpg")
            p.write_bytes(b"j")
            out.append(Frame(p, float(k)))
        return out

    monkeypatch.setattr(send, "extract_frames", fake_extract)
    from backend.agent.video.audio import TranscriptResult

    monkeypatch.setattr(send, "transcribe_video", lambda path: TranscriptResult("", "No audio track"))
    row = {"kind": "video", "name": "bug.mp4", "mime": "video/mp4", "path": "attachments/1_0_bug.mp4", "size_bytes": 1}
    return conv_dir, row, calls


def test_frames_added_for_non_gemini(tmp_path, monkeypatch):
    conv_dir, row, calls = _setup(tmp_path, monkeypatch)
    statuses = []
    send.prepare_video_frames([row], conv_dir=conv_dir, provider="anthropic", external=False, push_status=statuses.append)
    assert calls and calls[0][1] == 20
    assert row["frames"][0] == {"path": "attachments/1_0_bug.mp4.f20-01.jpg", "t_s": 0.0}
    assert any("Extracting frames" in s for s in statuses)


def test_gemini_small_mp4_gets_no_frames(tmp_path, monkeypatch):
    conv_dir, row, calls = _setup(tmp_path, monkeypatch)
    send.prepare_video_frames([row], conv_dir=conv_dir, provider="gemini", external=False)
    assert calls == [] and "frames" not in row


def test_image_cap_counts_frames(tmp_path, monkeypatch):
    conv_dir, row, _ = _setup(tmp_path, monkeypatch)
    from frontend.settings import PanelSettings

    s = PanelSettings.load()
    s.max_images_per_message = 40
    s.save()
    images = [{"kind": "image", "path": "attachments/x.png"}] * 25
    with pytest.raises(VideoError, match="Too many images"):
        send.prepare_video_frames([row, *images], conv_dir=conv_dir, provider="openai", external=False)


def test_runtime_dict_uses_absolute_paths(tmp_path, monkeypatch):
    conv_dir, row, _ = _setup(tmp_path, monkeypatch)
    row["frames"] = [{"path": "attachments/f.jpg", "t_s": 1.5}]
    out = send.runtime_video_dict(row, conv_dir)
    assert out == {
        "kind": "video", "name": "bug.mp4", "mime": "video/mp4",
        "abs_path": str(conv_dir / "attachments/1_0_bug.mp4"),
        "transcript": "",
        "frames": [{"abs_path": str(conv_dir / "attachments/f.jpg"), "t_s": 1.5}],
    }


def test_collect_image_paths_includes_video_frames(tmp_path, monkeypatch):
    from backend.agent.coding_agents import runner as ca

    conv_dir = tmp_path / "convs" / "c1"
    (conv_dir / "attachments").mkdir(parents=True)
    (conv_dir / "attachments" / "f1.jpg").write_bytes(b"j")
    monkeypatch.setattr("frontend.ui_web.project_chats.get_conversations_dir", lambda root=None: tmp_path / "convs")
    conv = SimpleNamespace(id="c1", messages=[{"role": "user", "attachments": [
        {"kind": "video", "path": "attachments/v.mp4", "frames": [{"path": "attachments/f1.jpg", "t_s": 0.5}]},
    ]}])
    assert ca.collect_image_paths(conv) == [str((conv_dir / "attachments" / "f1.jpg").resolve())]


def _hist(row):
    return [
        {"role": "assistant", "content": "x", "attachments": [dict(row)]},
        {"role": "user", "content": "hi", "attachments": [row, {"kind": "image", "path": "attachments/x.png"}]},
    ]


def test_backfill_adds_frames_when_provider_switched(tmp_path, monkeypatch):
    conv_dir, row, calls = _setup(tmp_path, monkeypatch)
    msgs = _hist(row)
    statuses = []
    assert send.backfill_history_frames(msgs, conv_dir=conv_dir, provider="anthropic", external=False, push_status=statuses.append) is True
    assert row["frames"] and len(calls) == 1
    assert "frames" not in msgs[0]["attachments"][0]
    assert msgs[1]["attachments"][1] == {"kind": "image", "path": "attachments/x.png"}
    assert any("Extracting frames" in s for s in statuses)


def test_backfill_skips_gemini(tmp_path, monkeypatch):
    conv_dir, row, calls = _setup(tmp_path, monkeypatch)
    assert send.backfill_history_frames(_hist(row), conv_dir=conv_dir, provider="gemini", external=False) is False
    assert calls == [] and "frames" not in row


def test_backfill_skips_existing_frames(tmp_path, monkeypatch):
    conv_dir, row, calls = _setup(tmp_path, monkeypatch)
    row["frames"] = [{"path": "attachments/f.jpg", "t_s": 0.0}]
    row["transcript_note"] = "No audio track"
    assert send.backfill_history_frames(_hist(row), conv_dir=conv_dir, provider="anthropic", external=False) is False
    assert calls == []


def test_backfill_survives_video_error(tmp_path, monkeypatch):
    conv_dir, row, _ = _setup(tmp_path, monkeypatch)

    def boom(path, n):
        raise VideoError("bad file")

    monkeypatch.setattr(send, "extract_frames", boom)
    statuses = []
    assert send.backfill_history_frames(_hist(row), conv_dir=conv_dir, provider="anthropic", external=False, push_status=statuses.append) is False
    assert "frames" not in row
    assert any("Could not read" in s for s in statuses)


def test_frame_count_follows_model(tmp_path, monkeypatch):
    from backend.agent.model_fetch import ModelInfo

    conv_dir, row, calls = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("backend.agent.model_fetch.get_model_info", lambda p, m: None)
    send.prepare_video_frames([row], conv_dir=conv_dir, provider="openai", external=False, model="gpt")
    assert calls[-1][1] == 20
    row2 = dict(row)
    monkeypatch.setattr(
        "backend.agent.model_fetch.get_model_info", lambda p, m: ModelInfo(id=m, max_images=8)
    )
    send.prepare_video_frames([row2], conv_dir=conv_dir, provider="openai", external=False, model="tiny")
    assert calls[-1][1] == 8


def test_needs_frames_gemini_without_video_support(monkeypatch):
    from backend.agent.model_fetch import ModelInfo
    from backend.agent.video.routing import needs_frames

    monkeypatch.setattr(
        "backend.agent.model_fetch.get_model_info", lambda p, m: ModelInfo(id=m, supports_video=False)
    )
    assert needs_frames("video/mp4", 1, provider="gemini", external=False, model="g")
    monkeypatch.setattr(
        "backend.agent.model_fetch.get_model_info", lambda p, m: ModelInfo(id=m, supports_video=None)
    )
    assert not needs_frames("video/mp4", 1, provider="gemini", external=False, model="g")


def _patch_tr(monkeypatch, result=None):
    from backend.agent.video.audio import TranscriptResult

    calls = []

    def fake(path):
        calls.append(path)
        return result or TranscriptResult("hi there", "")

    monkeypatch.setattr(send, "transcribe_video", fake)
    return calls


def test_prepare_sets_transcript_fields(tmp_path, monkeypatch):
    conv_dir, row, _ = _setup(tmp_path, monkeypatch)
    calls = _patch_tr(monkeypatch)
    statuses = []
    send.prepare_video_frames([row], conv_dir=conv_dir, provider="anthropic", external=False, push_status=statuses.append)
    assert row["transcript"] == "hi there" and row["transcript_note"] == ""
    assert any("Transcribing audio from bug.mp4" in s for s in statuses)
    send.prepare_video_frames([row], conv_dir=conv_dir, provider="anthropic", external=False)
    assert len(calls) == 1  # already has transcript_note


def test_prepare_skips_transcript_for_gemini_native(tmp_path, monkeypatch):
    conv_dir, row, _ = _setup(tmp_path, monkeypatch)
    calls = _patch_tr(monkeypatch)
    send.prepare_video_frames([row], conv_dir=conv_dir, provider="gemini", external=False)
    assert calls == [] and "transcript" not in row


def test_backfill_sets_transcript(tmp_path, monkeypatch):
    conv_dir, row, _ = _setup(tmp_path, monkeypatch)
    _patch_tr(monkeypatch)
    assert send.backfill_history_frames(_hist(row), conv_dir=conv_dir, provider="anthropic", external=False)
    assert row["transcript"] == "hi there"


def test_backfill_adds_transcript_to_rows_that_already_have_frames(tmp_path, monkeypatch):
    conv_dir, row, calls = _setup(tmp_path, monkeypatch)
    row["frames"] = [{"path": "attachments/f.jpg", "t_s": 0.0}]
    _patch_tr(monkeypatch)
    assert send.backfill_history_frames(_hist(row), conv_dir=conv_dir, provider="anthropic", external=False) is True
    assert row["transcript"] == "hi there" and calls == []


def test_runtime_dict_carries_transcript(tmp_path, monkeypatch):
    conv_dir, row, _ = _setup(tmp_path, monkeypatch)
    row["transcript"] = "yo"
    assert send.runtime_video_dict(row, conv_dir)["transcript"] == "yo"


def test_external_hint_includes_transcript(tmp_path):
    row = {"kind": "video", "path": "attachments/v.mp4", "transcript": "say cheese"}
    hint = send.external_video_hint(row, tmp_path)
    assert hint.startswith(f"Video file: {tmp_path / 'attachments/v.mp4'}")
    assert hint.splitlines()[1:] == ['Transcript of video:', 'say cheese']
    assert send.external_video_hint({"kind": "video", "path": "a.mp4"}, tmp_path) == f"Video file: {tmp_path / 'a.mp4'}"


def test_row_with_not_ready_note_starts_no_transcription(tmp_path, monkeypatch):
    conv_dir, row, _ = _setup(tmp_path, monkeypatch)
    calls = _patch_tr(monkeypatch)
    row["transcript_note"] = "Transcript not ready when sent"
    send.prepare_video_frames([row], conv_dir=conv_dir, provider="anthropic", external=False)
    assert calls == [] and row["transcript_note"] == "Transcript not ready when sent"
