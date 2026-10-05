from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest
from glob import escape as glob_escape

from backend.agent.video import audio, frames, prep


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    prep._jobs.clear()
    monkeypatch.setattr(prep, "resolve_staged", lambda sid: tmp_path / sid)
    monkeypatch.setattr(prep, "ensure_installed", lambda: (tmp_path / "ffmpeg", tmp_path / "ffprobe"))
    yield
    prep._jobs.clear()


def _wait(sid, states=("ready", "error"), timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        st = prep.prep_status(sid)
        if st["state"] in states:
            return st
        time.sleep(0.01)
    raise AssertionError(prep.prep_status(sid))


def test_progress_ready_and_transcript(monkeypatch, tmp_path):
    gate, seen = threading.Event(), threading.Event()
    vid = tmp_path / ("a" * 32 + ".mp4")

    def fake_extract(video, n, **kw):
        paths = frames.frame_paths(video, n)
        for p in paths[:3]:
            p.write_bytes(b"j")
        seen.set()
        gate.wait(5)
        for p in paths[3:]:
            p.write_bytes(b"j")
        return [frames.Frame(p, 0.0) for p in paths]

    monkeypatch.setattr(frames, "extract_frames", fake_extract)
    monkeypatch.setattr(audio, "transcribe_video", lambda v, **k: audio.TranscriptResult("hi", ""))
    st = prep.start_prep(vid.name, frames=5, transcribe=True)
    assert st["state"] in ("queued", "extracting")
    assert seen.wait(5)
    mid = prep.prep_status(vid.name)
    assert (mid["state"], mid["frames_done"], mid["frames_total"]) == ("extracting", 3, 5)
    gate.set()
    done = _wait(vid.name)
    assert done == {"state": "ready", "frames_done": 5, "frames_total": 5,
                    "transcript": "ok", "transcript_note": "", "error": "", "sendable": True}


def test_transcript_note_and_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(frames, "extract_frames", lambda v, n, **k: [])
    monkeypatch.setattr(audio, "transcribe_video", lambda v, **k: audio.TranscriptResult("", "No audio track"))
    prep.start_prep("n1.mp4", frames=2, transcribe=True)
    st = _wait("n1.mp4")
    assert (st["transcript"], st["transcript_note"]) == ("none", "No audio track")
    monkeypatch.setattr(audio, "transcribe_video", lambda v, **k: (_ for _ in ()).throw(AssertionError))
    prep.start_prep("n2.mp4", frames=2, transcribe=False)
    assert _wait("n2.mp4")["transcript"] == "skipped"


def test_error_then_retry(monkeypatch):
    calls = []

    def boom(v, n, **k):
        calls.append(n)
        if len(calls) == 1:
            raise frames.VideoError("Cannot read video 'x'.")
        return []

    monkeypatch.setattr(frames, "extract_frames", boom)
    monkeypatch.setattr(audio, "transcribe_video", lambda v, **k: audio.TranscriptResult("", ""))
    prep.start_prep("e.mp4", frames=4, transcribe=True)
    st = _wait("e.mp4")
    assert st["state"] == "error" and st["error"] == "Cannot read video 'x'."
    prep.retry_prep("e.mp4")
    assert _wait("e.mp4")["state"] == "ready"
    assert calls == [4, 4]


def test_idempotent_start(monkeypatch):
    gate, calls = threading.Event(), []

    def slow(v, n, **k):
        calls.append(1)
        gate.wait(5)
        return []

    monkeypatch.setattr(frames, "extract_frames", slow)
    monkeypatch.setattr(audio, "transcribe_video", lambda v, **k: audio.TranscriptResult("", ""))
    prep.start_prep("i.mp4", frames=2, transcribe=True)
    prep.start_prep("i.mp4", frames=2, transcribe=True)
    gate.set()
    _wait("i.mp4")
    prep.start_prep("i.mp4", frames=2, transcribe=True)
    time.sleep(0.05)
    assert calls == [1]


def test_concurrency_cap(monkeypatch):
    gate, lock, active, peak = threading.Event(), threading.Lock(), [0], [0]

    def slow(v, n, **k):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        gate.wait(5)
        with lock:
            active[0] -= 1
        return []

    monkeypatch.setattr(frames, "extract_frames", slow)
    monkeypatch.setattr(audio, "transcribe_video", lambda v, **k: audio.TranscriptResult("", ""))
    ids = [f"c{i}.mp4" for i in range(4)]
    for i in ids:
        prep.start_prep(i, frames=1, transcribe=True)
    time.sleep(0.3)
    assert peak[0] == 2
    assert sum(prep.prep_status(i)["state"] == "queued" for i in ids) == 2
    gate.set()
    for i in ids:
        _wait(i)
    assert peak[0] == 2


def test_unknown_id_is_ready():
    assert prep.prep_status("zzz.mp4")["state"] == "ready"


def test_expired_staged_id_errors(monkeypatch):
    def gone(sid):
        raise frames.VideoError("The video upload expired — attach the video again.")

    monkeypatch.setattr(prep, "resolve_staged", gone)
    st = prep.start_prep("g.mp4", frames=2, transcribe=True)
    assert st["state"] == "error"


def test_prep_files_satisfy_send_path_with_no_ffmpeg(monkeypatch, tmp_path):
    """Frames + transcript from prep survive persist's sibling rename; extract_frames then runs no ffmpeg."""
    from frontend.ui_web.conversation_attachments import persist_message_attachments
    from backend.agent.message_attachment import MessageAttachment

    staged = tmp_path / ("b" * 32 + ".mp4")
    staged.write_bytes(b"vid")
    ffmpeg, ffprobe = tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe"
    monkeypatch.setattr(frames, "ensure_installed", lambda: (ffmpeg, ffprobe))
    monkeypatch.setattr(audio, "ensure_installed", lambda: (ffmpeg, ffprobe))
    monkeypatch.setattr(prep, "ensure_installed", lambda: (ffmpeg, ffprobe))
    calls: list[list[str]] = []

    def runner(args, timeout):
        calls.append(args)
        if Path(args[0]) == ffprobe:
            out = "1\n"
            return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")
        Path(args[-1]).write_bytes(b"x")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(frames, "_runner", runner)
    monkeypatch.setattr("backend.voice.transcription.transcribe_audio", lambda b, m: {"ok": True, "text": "hello"})
    monkeypatch.setattr("backend.voice.transcription.openai_transcription_available", lambda: True)
    prep.start_prep(staged.name, frames=3, transcribe=True)
    assert _wait(staged.name)["state"] == "ready"
    calls.clear()

    att = MessageAttachment(kind="video", name="clip.mp4", mime="video/mp4", file_path=str(staged), size_bytes=3)
    rows = persist_message_attachments("conv1", 1.5, [att], tmp_path / "convs")
    persisted = (tmp_path / "convs" / "conv1" / rows[0]["path"])
    assert persisted.is_file()
    got = frames.extract_frames(persisted, 3)
    assert len(got) == 3
    assert audio.transcribe_video(persisted) == audio.TranscriptResult("hello", "")
    assert [c for c in calls if Path(c[0]) == ffmpeg] == []  # cheap ffprobe duration read only


def test_preparing_ffmpeg_state_precedes_extracting(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(prep, "ensure_installed", lambda: gate.wait(5) or ("a", "b"))
    monkeypatch.setattr(frames, "extract_frames", lambda v, n, **k: [])
    monkeypatch.setattr(audio, "transcribe_video", lambda v, **k: audio.TranscriptResult("", ""))
    prep.start_prep("f.mp4", frames=2, transcribe=True)
    assert _wait("f.mp4", states=("preparing_ffmpeg",))["state"] == "preparing_ffmpeg"
    gate.set()
    assert _wait("f.mp4")["state"] == "ready"


def test_ffmpeg_install_failure_is_an_error(monkeypatch):
    from backend.agent.video.ffmpeg_install import FfmpegInstallError

    def fail():
        raise FfmpegInstallError("download failed")

    monkeypatch.setattr(prep, "ensure_installed", fail)
    monkeypatch.setattr(frames, "extract_frames", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    prep.start_prep("h.mp4", frames=2, transcribe=True)
    st = _wait("h.mp4")
    assert st["state"] == "error" and st["error"] == "download failed"


def test_concurrent_start_spawns_one_worker(monkeypatch):
    runs, gate = [], threading.Event()

    def fake(v, n, **k):
        runs.append(1)
        gate.wait(5)
        return []

    monkeypatch.setattr(frames, "extract_frames", fake)
    monkeypatch.setattr(audio, "transcribe_video", lambda v, **k: audio.TranscriptResult("", ""))
    barrier = threading.Barrier(8)

    def go():
        barrier.wait()
        prep.start_prep("t.mp4", frames=2, transcribe=True)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    time.sleep(0.2)
    gate.set()
    _wait("t.mp4")
    assert runs == [1]


def test_persist_during_inflight_prep_copies_no_siblings(tmp_path):
    from backend.agent.message_attachment import MessageAttachment
    from frontend.ui_web.conversation_attachments import persist_message_attachments

    staged = tmp_path / ("c" * 32 + ".mp4")
    staged.write_bytes(b"v")
    for suffix in (".f02-01.jpg", ".f02-02.jpg.part.jpg"):
        staged.with_name(staged.name + suffix).write_bytes(b"half")
    with prep._lock:
        prep._jobs[staged.name] = {**prep._new_job(2, True), "state": "extracting", "path": staged}
    att = MessageAttachment(kind="video", name="c.mp4", mime="video/mp4", file_path=str(staged), size_bytes=1)
    rows = persist_message_attachments("conv2", 1.0, [att], tmp_path / "convs")
    out = tmp_path / "convs" / "conv2" / "attachments"
    assert [p.name for p in out.iterdir()] == [Path(rows[0]["path"]).name]


def test_persist_skips_part_files_when_ready(tmp_path):
    from backend.agent.message_attachment import MessageAttachment
    from frontend.ui_web.conversation_attachments import persist_message_attachments

    staged = tmp_path / ("d" * 32 + ".mp4")
    staged.write_bytes(b"v")
    staged.with_name(staged.name + ".f02-01.jpg").write_bytes(b"ok")
    staged.with_name(staged.name + ".f02-02.jpg.part.jpg").write_bytes(b"half")
    att = MessageAttachment(kind="video", name="d.mp4", mime="video/mp4", file_path=str(staged), size_bytes=1)
    persist_message_attachments("conv3", 1.0, [att], tmp_path / "convs")
    names = [p.name for p in (tmp_path / "convs" / "conv3" / "attachments").iterdir()]
    assert any(n.endswith(".f02-01.jpg") for n in names)
    assert not any(n.endswith(".part.jpg") for n in names)


def _blocking_transcribe(monkeypatch, gate, *, release=True):
    def fake(v, on_extracted=None, **k):
        if release and on_extracted:
            on_extracted()
        gate.wait(5)
        return audio.TranscriptResult("late", "")

    monkeypatch.setattr(audio, "transcribe_video", fake)


def test_sendable_after_frames_while_transcription_runs(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(frames, "extract_frames", lambda v, n, **k: [frames.Frame(Path("a"), 0.0)])
    _blocking_transcribe(monkeypatch, gate)
    prep.start_prep("s.mp4", frames=1, transcribe=True)
    st = _wait("s.mp4", states=("transcribing",))
    assert st["state"] == "transcribing" and st["sendable"] is True
    gate.set()
    done = _wait("s.mp4")
    assert done["state"] == "ready" and done["sendable"] is True


def test_not_sendable_while_extracting(monkeypatch):
    gate = threading.Event()

    def slow(v, n, **k):
        gate.wait(5)
        return []

    monkeypatch.setattr(frames, "extract_frames", slow)
    monkeypatch.setattr(audio, "transcribe_video", lambda v, **k: audio.TranscriptResult("", ""))
    prep.start_prep("x.mp4", frames=1, transcribe=True)
    st = _wait("x.mp4", states=("extracting",))
    assert st["sendable"] is False
    gate.set()
    _wait("x.mp4")


def test_slot_released_before_transcription_network_call(monkeypatch):
    gate = threading.Event()
    extracting = threading.Event()

    def extract(v, n, **k):
        if Path(v).name == "j3.mp4":
            extracting.set()
        return []

    monkeypatch.setattr(frames, "extract_frames", extract)
    _blocking_transcribe(monkeypatch, gate)
    for i in ("j1.mp4", "j2.mp4", "j3.mp4"):
        prep.start_prep(i, frames=1, transcribe=True)
    assert extracting.wait(3)  # third job got a slot while the first two are still transcribing
    assert prep.prep_status("j1.mp4")["state"] == "transcribing"
    gate.set()
    for i in ("j1.mp4", "j2.mp4", "j3.mp4"):
        _wait(i)


def test_persist_while_transcribing_copies_frames_and_sets_note(monkeypatch, tmp_path):
    from backend.agent.message_attachment import MessageAttachment
    from frontend.ui_web.conversation_attachments import persist_message_attachments

    staged = tmp_path / ("c" * 32 + ".mp4")
    staged.write_bytes(b"vid")
    gate = threading.Event()
    got = []

    def extract(video, n, **k):
        paths = frames.frame_paths(video, n)
        for p in paths:
            p.write_bytes(b"j")
        got.extend(paths)
        return [frames.Frame(p, 0.0) for p in paths]

    monkeypatch.setattr(frames, "extract_frames", extract)
    _blocking_transcribe(monkeypatch, gate)
    prep.start_prep(staged.name, frames=2, transcribe=True)
    assert _wait(staged.name, states=("transcribing",))["sendable"] is True
    (tmp_path / (staged.name + ".audio.mp3")).write_bytes(b"m")
    att = MessageAttachment(kind="video", name="clip.mp4", mime="video/mp4", file_path=str(staged), size_bytes=3)
    rows = persist_message_attachments("conv1", 1.5, [att], tmp_path / "convs")
    full = tmp_path / "convs" / "conv1" / rows[0]["path"]
    copied = sorted(p.name[len(full.name):] for p in full.parent.glob(glob_escape(full.name) + ".*"))
    assert len(copied) == 2 and all(c.endswith(".jpg") and not c.endswith(".part.jpg") for c in copied)
    assert rows[0]["transcript_note"] == "Transcript not ready when sent"
    gate.set()
    _wait(staged.name)
