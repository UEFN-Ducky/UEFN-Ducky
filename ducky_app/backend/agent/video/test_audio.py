from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from backend.agent.video import audio


@pytest.mark.skipif(not os.environ.get("DUCKY_FFMPEG_DIR"), reason="needs real ffmpeg")
def test_real_ffmpeg_extracts_audio(tmp_path):
    ffmpeg = Path(os.environ["DUCKY_FFMPEG_DIR"]) / "ffmpeg.exe"
    video = tmp_path / "tone.mp4"
    subprocess.run(
        [str(ffmpeg), "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest", "-y", str(video)],
        check=True,
    )
    out = audio.extract_mp3(ffmpeg, video, tmp_path / "o.mp3")
    assert out.is_file() and out.stat().st_size > 0
