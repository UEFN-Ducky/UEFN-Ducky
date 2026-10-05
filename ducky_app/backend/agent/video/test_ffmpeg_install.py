from __future__ import annotations

import hashlib
import io
import urllib.error
import zipfile

import pytest

from backend.agent.video import ffmpeg_install as fi


def _fake_zip(*, with_probe: bool = True) -> bytes:
    root = fi.FFMPEG_ZIP_ROOT
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{root}/bin/ffmpeg.exe", b"ffmpeg")
        if with_probe:
            zf.writestr(f"{root}/bin/ffprobe.exe", b"ffprobe")
        zf.writestr(f"{root}/bin/avcodec-63.dll", b"dll")
        zf.writestr(f"{root}/bin/ffplay.exe", b"skip me")
        zf.writestr(f"{root}/LICENSE.txt", b"LGPL")
        zf.writestr(f"{root}/doc/readme.html", b"skip me")
    return buf.getvalue()


class _Resp(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def _serve(monkeypatch, data: bytes, *, sha: str | None = None):
    monkeypatch.setattr(fi, "FFMPEG_SHA256", sha or hashlib.sha256(data).hexdigest())
    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append(req)
        return _Resp(data)

    monkeypatch.setattr(fi, "_urlopen", fake_urlopen)
    return calls


@pytest.fixture(autouse=True)
def _reset_state():
    fi._set(state="missing", progress=0.0, error="")
    yield


def test_install_extracts_only_needed_files(monkeypatch):
    _serve(monkeypatch, _fake_zip())
    ffmpeg, ffprobe = fi.ensure_installed()
    d = fi.install_dir()
    assert ffmpeg == d / "ffmpeg.exe" and ffprobe == d / "ffprobe.exe"
    assert sorted(p.name for p in d.iterdir()) == [
        ".installed", "LICENSE.txt", "avcodec-63.dll", "ffmpeg.exe", "ffprobe.exe",
    ]
    assert fi.status()["state"] == "ready"
    assert not list(fi.tools_root().glob("*.part"))


def test_second_call_does_not_download_again(monkeypatch):
    calls = _serve(monkeypatch, _fake_zip())
    fi.ensure_installed()
    fi.ensure_installed()
    assert len(calls) == 1


def test_checksum_mismatch_never_installs(monkeypatch):
    _serve(monkeypatch, _fake_zip(), sha="0" * 64)
    with pytest.raises(fi.FfmpegInstallError, match="corrupted"):
        fi.ensure_installed()
    assert fi.binaries() is None
    assert not fi.install_dir().exists()
    assert not list(fi.tools_root().glob("*.part"))
    st = fi.status()
    assert st["state"] == "error" and "corrupted" in st["error"]


def test_archive_without_ffprobe_is_rejected(monkeypatch):
    _serve(monkeypatch, _fake_zip(with_probe=False))
    with pytest.raises(fi.FfmpegInstallError, match="missing"):
        fi.ensure_installed()
    assert fi.binaries() is None


def test_404_says_update_the_app(monkeypatch):
    def gone(req, timeout=0):
        raise urllib.error.HTTPError(fi.FFMPEG_URL, 404, "Not Found", {}, None)

    monkeypatch.setattr(fi, "_urlopen", gone)
    with pytest.raises(fi.FfmpegInstallError, match="update UEFN-Ducky"):
        fi.ensure_installed()


def test_offline_says_check_connection(monkeypatch):
    def offline(req, timeout=0):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(fi, "_urlopen", offline)
    with pytest.raises(fi.FfmpegInstallError, match="internet"):
        fi.ensure_installed()


def test_remove_deletes_install(monkeypatch):
    _serve(monkeypatch, _fake_zip())
    fi.ensure_installed()
    assert fi.remove()["state"] == "missing"
    assert fi.binaries() is None


def test_start_install_runs_in_background(monkeypatch):
    _serve(monkeypatch, _fake_zip())
    first = fi.start_install()
    assert first["state"] in ("installing", "ready")
    fi._thread.join(timeout=10)
    assert fi.status()["state"] == "ready"


def _bundle(root):
    d = root / "tools" / "ffmpeg"
    d.mkdir(parents=True)
    (d / "ffmpeg.exe").write_bytes(b"x")
    (d / "ffprobe.exe").write_bytes(b"x")
    return d


def test_binaries_prefer_bundled(monkeypatch, tmp_path):
    d = _bundle(tmp_path)
    monkeypatch.setattr(fi, "packaged_data_root", lambda: tmp_path)
    assert fi.bundled_dir() == d
    assert fi.binaries() == (d / "ffmpeg.exe", d / "ffprobe.exe")
    st = fi.status()
    assert st["state"] == "ready" and st["bundled"] is True and st["version"] == fi.FFMPEG_VERSION


def test_bundled_dir_none_when_not_packaged_or_incomplete(monkeypatch, tmp_path):
    monkeypatch.setattr(fi, "packaged_data_root", lambda: None)
    assert fi.bundled_dir() is None
    assert "bundled" not in fi.status() or fi.status()["bundled"] is False
    (tmp_path / "tools" / "ffmpeg").mkdir(parents=True)
    monkeypatch.setattr(fi, "packaged_data_root", lambda: tmp_path)
    assert fi.bundled_dir() is None


def test_remove_never_touches_bundled(monkeypatch, tmp_path):
    d = _bundle(tmp_path)
    monkeypatch.setattr(fi, "packaged_data_root", lambda: tmp_path)
    fi.remove()
    assert (d / "ffmpeg.exe").is_file()
    assert fi.status()["bundled"] is True
