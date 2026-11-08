from __future__ import annotations

from types import SimpleNamespace

from backend.agent.video import send


def _setup(tmp_path):
    conv_dir = tmp_path / "conv1"
    (conv_dir / "attachments").mkdir(parents=True)
    (conv_dir / "attachments" / "1_0_bug.mp4").write_bytes(b"v")
    row = {"kind": "video", "name": "bug.mp4", "mime": "video/mp4", "path": "attachments/1_0_bug.mp4", "size_bytes": 1}
    return conv_dir, row


def test_runtime_dict_uses_absolute_paths(tmp_path):
    conv_dir, row = _setup(tmp_path)
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
    monkeypatch.setattr("frontend.ui_web.chats.store.get_conversations_dir", lambda root=None: tmp_path / "convs")
    conv = SimpleNamespace(id="c1", messages=[{"role": "user", "attachments": [
        {"kind": "video", "path": "attachments/v.mp4", "frames": [{"path": "attachments/f1.jpg", "t_s": 0.5}]},
    ]}])
    assert ca.collect_image_paths(conv) == [str((conv_dir / "attachments" / "f1.jpg").resolve())]


def test_runtime_dict_carries_transcript(tmp_path):
    conv_dir, row = _setup(tmp_path)
    row["transcript"] = "yo"
    assert send.runtime_video_dict(row, conv_dir)["transcript"] == "yo"


def test_external_hint_includes_transcript(tmp_path):
    row = {"kind": "video", "path": "attachments/v.mp4", "transcript": "say cheese"}
    hint = send.external_video_hint(row, tmp_path)
    assert hint.startswith(f"Video file: {tmp_path / 'attachments/v.mp4'}")
    assert hint.splitlines()[1:] == ['Transcript of video:', 'say cheese']
    bare = send.external_video_hint({"kind": "video", "path": "a.mp4"}, tmp_path)
    assert bare.startswith(f"Video file: {tmp_path / 'a.mp4'} (referenced, not sent to you).")
    assert f'web_video(url="{tmp_path / "a.mp4"}", overview=8)' in bare and "\n" not in bare
