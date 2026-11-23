import hashlib
import io
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_appimage as ba  # noqa: E402


def _can_symlink(tmp_path: Path) -> bool:
    try:
        (tmp_path / "probe").symlink_to("x")
        (tmp_path / "probe").unlink()
        return True
    except OSError:  # Windows without Developer Mode
        return False


def _one_dir_build(tmp_path: Path) -> Path:
    app = tmp_path / "dist" / "uefn-ducky"
    (app / "_internal").mkdir(parents=True)
    for name in ("uefn-ducky", "uefn-ducky-bridge", "Ducky-Launcher", "_internal/libportaudio.so.2"):
        (app / name).write_bytes(b"\x7fELF")
    return app


def test_names_and_update_info() -> None:
    assert ba.appimage_name("1.2.500", "x86_64") == "UEFN-Ducky-1.2.500-x86_64.AppImage"
    assert ba.appimage_name("1.2.500", "aarch64") == "UEFN-Ducky-1.2.500-aarch64.AppImage"
    assert ba.update_info("aarch64", "https://uefnducky.org/downloads/linux/") == (
        "zsync|https://uefnducky.org/downloads/linux/UEFN-Ducky-latest-aarch64.AppImage.zsync"
    )
    assert ba.normalize_arch("amd64") == "x86_64" and ba.normalize_arch("arm64") == "aarch64"
    with pytest.raises(SystemExit):
        ba.normalize_arch("i686")


def test_version_comes_from_the_app(tmp_path: Path) -> None:
    init = tmp_path / "ducky_app" / "frontend" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text('__version__ = "1.2.77"\n', encoding="utf-8")
    assert ba.read_version(tmp_path) == "1.2.77"
    assert ba.read_version() == ba.read_version(ba.ROOT)  # the real tree parses


def test_appimagetool_gets_the_update_info_and_no_fuse(tmp_path: Path, monkeypatch) -> None:
    cmd = ba.appimagetool_cmd(Path("/t/tool"), Path("/a"), Path("/o.AppImage"), "x86_64")
    assert cmd[cmd.index("-u") + 1] == ba.update_info("x86_64")
    calls = []

    def run(cmd, check=True, env=None, cwd=None):
        calls.append((cmd, env))
        out = Path(cmd[-1])
        out.write_bytes(b"AI")
        out.with_name(out.name + ".zsync").write_bytes(b"zsync")

    if not _can_symlink(tmp_path):
        pytest.skip("symlinks unavailable")
    monkeypatch.setattr(ba, "fetch_appimagetool", lambda arch, cache: Path("/t/tool"))
    out = ba.build(_one_dir_build(tmp_path), tmp_path / "dist", arch="aarch64", version="1.2.3",
                   cache=tmp_path / "c", run=run)
    assert out.name == "UEFN-Ducky-1.2.3-aarch64.AppImage"
    assert calls[0][1]["ARCH"] == "aarch64" and calls[0][1]["APPIMAGE_EXTRACT_AND_RUN"] == "1"
    assert not (tmp_path / "dist" / "UEFN-Ducky-aarch64.AppDir").exists()


def test_appdir_layout(tmp_path: Path) -> None:
    if not _can_symlink(tmp_path):
        pytest.skip("symlinks unavailable")
    appdir = ba.assemble_appdir(_one_dir_build(tmp_path), tmp_path / "AppDir")
    for rel in ("AppRun", "uefn-ducky.desktop", "uefn-ducky.png", "usr/bin/uefn-ducky",
                "usr/bin/uefn-ducky-bridge", "usr/bin/Ducky-Launcher",
                "usr/share/applications/uefn-ducky.desktop",
                "usr/share/icons/hicolor/256x256/apps/uefn-ducky.png"):
        assert (appdir / rel).is_file(), rel
    assert b"\r\n" not in (appdir / "AppRun").read_bytes()
    assert os.readlink(appdir / ".DirIcon") == "uefn-ducky.png"
    # The microphone: usr/lib/libportaudio.so.2 is the bundled PortAudio.
    assert os.readlink(appdir / "usr" / "lib" / "libportaudio.so.2") == "../bin/_internal/libportaudio.so.2"
    assert (appdir / "usr" / "lib" / "libportaudio.so.2").resolve().is_file()
    if os.name != "nt":
        assert os.access(appdir / "AppRun", os.X_OK)


def test_build_without_portaudio_stops(tmp_path: Path) -> None:
    app = _one_dir_build(tmp_path)
    (app / "_internal" / "libportaudio.so.2").unlink()
    with pytest.raises(SystemExit, match="libportaudio"):
        ba.assemble_appdir(app, tmp_path / "AppDir")


def test_appimagetool_download_is_pinned(tmp_path: Path, monkeypatch) -> None:
    body = b"not the real tool"
    tmp_path = tmp_path / "cache"
    monkeypatch.setattr(ba.urllib.request, "urlopen", lambda url, timeout=0: io.BytesIO(body))
    with pytest.raises(SystemExit, match="SHA-256"):
        ba.fetch_appimagetool("x86_64", tmp_path)
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setitem(ba.APPIMAGETOOL_SHA256, "x86_64", hashlib.sha256(body).hexdigest())
    tool = ba.fetch_appimagetool("x86_64", tmp_path)
    assert tool.read_bytes() == body
    monkeypatch.setattr(ba.urllib.request, "urlopen", lambda *a, **k: pytest.fail("cached tool re-downloaded"))
    assert ba.fetch_appimagetool("x86_64", tmp_path) == tool


def test_apprun_starts_the_app_and_keeps_its_arguments() -> None:
    text = (HERE / "AppRun").read_text(encoding="utf-8")
    assert text.startswith("#!/bin/bash\n")
    assert 'exec "$HERE/usr/bin/$APP_ID" "$@"' in text
