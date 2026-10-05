import hashlib
import importlib.util
import io
import sys
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / "ducky_app"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

spec = importlib.util.spec_from_file_location("fetch_ffmpeg_tested", Path(__file__).with_name("fetch_ffmpeg.py"))
ff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ff)
fi = ff.fi


def _zip_bytes() -> bytes:
    root = fi.FFMPEG_ZIP_ROOT
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{root}/bin/ffmpeg.exe", b"ffmpeg")
        zf.writestr(f"{root}/bin/ffprobe.exe", b"ffprobe")
        zf.writestr(f"{root}/bin/avcodec-63.dll", b"dll")
        zf.writestr(f"{root}/bin/ffplay.exe", b"skip")
        zf.writestr(f"{root}/LICENSE.txt", b"LGPL")
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def _serve(monkeypatch, data, sha=None):
    monkeypatch.setattr(fi, "FFMPEG_SHA256", sha or hashlib.sha256(data).hexdigest())
    calls = []
    monkeypatch.setattr(fi, "_urlopen", lambda req, timeout=0: (calls.append(req), _Resp(data))[1])
    monkeypatch.delenv("DUCKY_FFMPEG_ZIP", raising=False)
    return calls


def test_ensure_bundle_downloads_and_extracts(monkeypatch, tmp_path):
    _serve(monkeypatch, _zip_bytes())
    dest = ff.ensure_bundle(tmp_path / "bundle")
    assert sorted(p.name for p in dest.iterdir()) == [
        ".installed", "LICENSE.txt", "avcodec-63.dll", "ffmpeg.exe", "ffprobe.exe",
    ]
    assert (dest / ".installed").read_text(encoding="utf-8") == fi.FFMPEG_VERSION


def test_ensure_bundle_reuses_matching_marker(monkeypatch, tmp_path):
    calls = _serve(monkeypatch, _zip_bytes())
    ff.ensure_bundle(tmp_path / "b")
    ff.ensure_bundle(tmp_path / "b")
    assert len(calls) == 1


def test_ensure_bundle_mismatch_raises(monkeypatch, tmp_path):
    _serve(monkeypatch, _zip_bytes(), sha="0" * 64)
    with pytest.raises(fi.FfmpegInstallError):
        ff.ensure_bundle(tmp_path / "b")
    assert not (tmp_path / "b" / ".installed").exists()


def test_local_zip_env_skips_network(monkeypatch, tmp_path):
    data = _zip_bytes()
    calls = _serve(monkeypatch, data)
    z = tmp_path / "ff.zip"
    z.write_bytes(data)
    monkeypatch.setenv("DUCKY_FFMPEG_ZIP", str(z))
    dest = ff.ensure_bundle(tmp_path / "b")
    assert calls == [] and (dest / "ffmpeg.exe").is_file()


def test_local_zip_with_wrong_sha_rejected(monkeypatch, tmp_path):
    _serve(monkeypatch, _zip_bytes(), sha="0" * 64)
    z = tmp_path / "ff.zip"
    z.write_bytes(_zip_bytes())
    monkeypatch.setenv("DUCKY_FFMPEG_ZIP", str(z))
    with pytest.raises(fi.FfmpegInstallError):
        ff.ensure_bundle(tmp_path / "b")
