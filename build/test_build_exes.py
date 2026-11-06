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


def test_search_model_present(tmp_path: Path) -> None:
    folder = tmp_path / "_internal" / "models" / "search"
    folder.mkdir(parents=True)
    for name in be.SEARCH_MODEL_FILES:
        (folder / name).write_bytes(b"x")
    assert be.missing_search_model_files(tmp_path) == []


def test_search_model_missing_is_reported(tmp_path: Path) -> None:
    (tmp_path / "_internal").mkdir()
    assert be.missing_search_model_files(tmp_path) == list(be.SEARCH_MODEL_FILES)


def _launcher_src(tmp_path: Path) -> Path:
    src = tmp_path / "native_launcher"
    (src / "app" / "src").mkdir(parents=True)
    (src / "Cargo.toml").write_text('[workspace]\nmembers=["app","crates/*"]\n', encoding="utf-8")
    (src / "app" / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    return src


class _FakeCargo:
    def __init__(self, rc: int = 0, dll: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc
        self.dll = dll

    def __call__(self, cmd, cwd=None):
        self.calls.append(list(cmd))
        target = Path(cmd[cmd.index("--target-dir") + 1])
        if self.rc == 0:
            (target / "release").mkdir(parents=True, exist_ok=True)
            (target / "release" / be.LAUNCHER_EXE).write_bytes(b"MZ launcher")
            if self.dll:
                out = target / "release" / "build" / "webview2-com-sys-0123abcd" / "out"
                for arch in ("x64", "x86", "arm64"):
                    (out / arch).mkdir(parents=True, exist_ok=True)
                    (out / arch / be.LAUNCHER_DLL).write_bytes(f"MZ loader {arch}".encode())

        class _P:
            returncode = self.rc

        return _P()


def test_launcher_hash_ignores_target_and_tracks_sources(tmp_path: Path) -> None:
    src = _launcher_src(tmp_path)
    first = be.launcher_source_hash(src)
    (src / "target" / "release").mkdir(parents=True)
    (src / "target" / "release" / "junk.o").write_bytes(b"x")
    (src / "app" / "target").mkdir()
    (src / "app" / "target" / "x").write_bytes(b"y")
    assert be.launcher_source_hash(src) == first
    assert all("target" not in p.relative_to(src).parts for p in be.launcher_source_files(src))
    (src / "app" / "src" / "main.rs").write_text("fn main() { }\n", encoding="utf-8")
    assert be.launcher_source_hash(src) != first
    crate = src / "crates" / "ipc" / "src"
    crate.mkdir(parents=True)
    (crate / "lib.rs").write_text("", encoding="utf-8")
    assert be.launcher_source_hash(src) != first


def test_launcher_hash_depends_on_path_not_just_bytes(tmp_path: Path) -> None:
    src = _launcher_src(tmp_path)
    (src / "a.rs").write_text("x", encoding="utf-8")
    one = be.launcher_source_hash(src)
    (src / "a.rs").rename(src / "b.rs")
    assert be.launcher_source_hash(src) != one


def test_launcher_cache_miss_runs_cargo_then_hit_skips_it(tmp_path: Path) -> None:
    src = _launcher_src(tmp_path)
    cache = tmp_path / "cache"
    cargo = _FakeCargo()
    first = be.build_launcher(src, cache_root=cache, cargo="cargo", run=cargo)
    assert first is not None and first.is_file()
    assert first.parent == cache / be.launcher_source_hash(src)
    assert len(cargo.calls) == 1
    assert cargo.calls[0][:3] == ["cargo", "build", "--release"]

    def _boom(*_a, **_k):
        raise AssertionError("cargo must not run on a cache hit")

    assert be.build_launcher(src, cache_root=cache, cargo="cargo", run=_boom) == first


def test_launcher_source_change_is_a_miss(tmp_path: Path) -> None:
    src = _launcher_src(tmp_path)
    cache = tmp_path / "cache"
    cargo = _FakeCargo()
    a = be.build_launcher(src, cache_root=cache, cargo="cargo", run=cargo)
    (src / "app" / "src" / "main.rs").write_text("fn main() { println!(); }\n", encoding="utf-8")
    b = be.build_launcher(src, cache_root=cache, cargo="cargo", run=cargo)
    assert len(cargo.calls) == 2 and a != b


def test_no_cargo_warns_and_builds_without_launcher(tmp_path: Path, monkeypatch, capsys) -> None:
    src = _launcher_src(tmp_path)
    monkeypatch.setattr(be.shutil, "which", lambda _name: None)
    assert be.build_launcher(src, cache_root=tmp_path / "cache") is None
    assert "WITHOUT Ducky-Launcher.exe" in capsys.readouterr().err
    app = tmp_path / "app"
    app.mkdir()
    assert be.place_launcher(None, app) is None
    assert not (app / be.LAUNCHER_EXE).exists()


def test_failed_cargo_build_ships_without_launcher(tmp_path: Path, capsys) -> None:
    src = _launcher_src(tmp_path)
    cache = tmp_path / "cache"
    assert be.build_launcher(src, cache_root=cache, cargo="cargo", run=_FakeCargo(rc=101)) is None
    assert "WITHOUT" in capsys.readouterr().err
    assert not (cache / be.launcher_source_hash(src)).exists()


def test_missing_source_tree_is_skipped(tmp_path: Path) -> None:
    assert be.build_launcher(tmp_path / "native_launcher", cache_root=tmp_path / "c", run=_FakeCargo()) is None


def test_launcher_is_placed_beside_the_app(tmp_path: Path) -> None:
    exe = tmp_path / "cached" / be.LAUNCHER_EXE
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    app = tmp_path / "UEFN-Ducky-1.2.3"
    app.mkdir()
    (app / "UEFN-Ducky.exe").write_bytes(b"MZ")
    (exe.parent / be.LAUNCHER_DLL).write_bytes(b"MZ dll")
    placed = be.place_launcher(exe, app)
    assert placed == app / "Ducky-Launcher.exe" and placed.read_bytes() == b"MZ"
    assert (app / "WebView2Loader.dll").read_bytes() == b"MZ dll"


def test_launcher_without_its_dll_is_not_placed(tmp_path: Path, capsys) -> None:
    exe = tmp_path / "cached" / be.LAUNCHER_EXE
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    app = tmp_path / "app"
    app.mkdir()
    assert be.place_launcher(exe, app) is None
    assert list(app.iterdir()) == [] and "WITHOUT" in capsys.readouterr().err


def test_cache_holds_the_x64_dll_next_to_the_exe(tmp_path: Path) -> None:
    src = _launcher_src(tmp_path)
    exe = be.build_launcher(src, cache_root=tmp_path / "cache", cargo="cargo", run=_FakeCargo())
    assert (exe.parent / "WebView2Loader.dll").read_bytes() == b"MZ loader x64"
    assert sorted(p.name for p in exe.parent.iterdir()) == ["Ducky-Launcher.exe", "WebView2Loader.dll"]


def test_cache_entry_without_the_dll_is_rebuilt(tmp_path: Path) -> None:
    src = _launcher_src(tmp_path)
    cache = tmp_path / "cache"
    old = cache / be.launcher_source_hash(src) / be.LAUNCHER_EXE
    old.parent.mkdir(parents=True)
    old.write_bytes(b"MZ old")
    cargo = _FakeCargo()
    assert be.build_launcher(src, cache_root=cache, cargo="cargo", run=cargo) == old
    assert len(cargo.calls) == 1 and (old.parent / be.LAUNCHER_DLL).is_file()


def test_build_without_the_dll_ships_without_launcher(tmp_path: Path, capsys) -> None:
    src = _launcher_src(tmp_path)
    cache = tmp_path / "cache"
    assert be.build_launcher(src, cache_root=cache, cargo="cargo", run=_FakeCargo(dll=False)) is None
    assert "WebView2Loader.dll" in capsys.readouterr().err
    assert not (cache / be.launcher_source_hash(src)).exists()


def test_cache_root_is_under_localappdata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert be.launcher_cache_root() == tmp_path / "UEFN-Ducky" / "build_cache" / "launcher"
