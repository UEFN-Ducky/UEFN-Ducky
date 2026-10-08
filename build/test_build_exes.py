import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("build_exes_tested", Path(__file__).with_name("build_exes.py"))
be = importlib.util.module_from_spec(spec)
spec.loader.exec_module(be)


def _app(tmp_path: Path, tools: list[str], top: list[str]) -> Path:
    internal = tmp_path / "_internal"
    ffmpeg = internal / "tools" / "ffmpeg"
    ffmpeg.mkdir(parents=True)
    for name in tools:
        (ffmpeg / name).write_bytes(b"x")
    for name in top:
        (internal / name).write_bytes(b"x")
    return tmp_path


def test_ffmpeg_dll_copied_to_the_top_is_a_duplicate(tmp_path: Path) -> None:
    app = _app(tmp_path, ["ffmpeg.exe", "avcodec-63.dll", "avutil-61.dll"], ["avcodec-63.dll", "python313.dll"])
    assert be.duplicate_ffmpeg_dlls(app) == ["avcodec-63.dll"]


def test_ffmpeg_dlls_only_in_tools_are_fine(tmp_path: Path) -> None:
    app = _app(tmp_path, ["ffmpeg.exe", "avcodec-63.dll"], ["python313.dll"])
    assert be.duplicate_ffmpeg_dlls(app) == []


def test_build_without_ffmpeg_is_fine(tmp_path: Path) -> None:
    (tmp_path / "_internal").mkdir()
    assert be.duplicate_ffmpeg_dlls(tmp_path) == []


def test_released_build_is_checked(tmp_path: Path) -> None:
    # The 1.2.344 layout: every ffmpeg DLL at the top as well.
    dlls = ["avcodec-63.dll", "avdevice-63.dll", "avfilter-12.dll", "avformat-63.dll"]
    app = _app(tmp_path, ["ffmpeg.exe", "ffprobe.exe", *dlls], dlls)
    assert be.duplicate_ffmpeg_dlls(app) == sorted(dlls)
