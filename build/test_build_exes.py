import importlib.util
import sys
from pathlib import Path

import pytest

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


def test_npm_install_is_skipped_only_when_node_modules_matches_the_lock(tmp_path: Path) -> None:
    import os

    lock = tmp_path / "package-lock.json"
    lock.write_text("{}")
    assert not be.node_modules_current(tmp_path)  # nothing installed
    installed = tmp_path / "node_modules" / ".package-lock.json"
    installed.parent.mkdir()
    installed.write_text("{}")
    os.utime(lock, (1000, 1000))
    os.utime(installed, (2000, 2000))
    assert be.node_modules_current(tmp_path)
    os.utime(lock, (3000, 3000))  # the lock changed after the install
    assert not be.node_modules_current(tmp_path)


def test_pip_is_skipped_for_the_same_requirements_within_a_day(tmp_path: Path) -> None:
    import os

    req = tmp_path / "requirements.txt"
    req.write_text("a==1\n")
    stamp = be.pip_stamp([req], python="py", stamp_dir=tmp_path / "s")
    assert not be.pip_fresh(stamp)
    stamp.parent.mkdir()
    stamp.write_text("ok")
    os.utime(stamp, (10_000, 10_000))
    assert be.pip_fresh(stamp, now=10_000 + 3600)
    assert not be.pip_fresh(stamp, now=10_000 + be.PIP_STAMP_MAX_AGE_S + 1)  # a day old: pip again
    req.write_text("a==2\n")
    assert be.pip_stamp([req], python="py", stamp_dir=tmp_path / "s") != stamp  # new pins, new stamp
    assert be.pip_stamp([req], python="other", stamp_dir=tmp_path / "s") != be.pip_stamp([req], python="py", stamp_dir=tmp_path / "s")


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


def test_webview_dlls_missing_from_the_build_are_reported(tmp_path: Path) -> None:
    lib = tmp_path / "_internal" / "webview" / "lib"
    for name in be.WEBVIEW_DLLS[1:]:
        (lib / name).parent.mkdir(parents=True, exist_ok=True)
        (lib / name).write_bytes(b"x")
    assert be.missing_webview_dlls(tmp_path) == ["Microsoft.Web.WebView2.Core.dll"]
    (lib / be.WEBVIEW_DLLS[0]).write_bytes(b"x")
    assert be.missing_webview_dlls(tmp_path) == []


def test_webview_dll_list_matches_the_start_repair() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ducky_app"))
    from frontend import webview2_repair

    assert be.WEBVIEW_DLLS == webview2_repair.DLLS


def _launcher_src(tmp_path: Path) -> Path:
    src = tmp_path / "native_launcher"
    (src / "app" / "src").mkdir(parents=True)
    (src / "Cargo.toml").write_text('[workspace]\nmembers=["app","crates/*"]\n', encoding="utf-8")
    (src / "app" / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    return src


class _FakeCargo:
    def __init__(self, rc: int = 0, dll: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict | None] = []
        self.rc = rc
        self.dll = dll

    def __call__(self, cmd, cwd=None, env=None):
        self.calls.append(list(cmd))
        self.envs.append(env)
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


def test_a_new_app_version_rebuilds_the_launcher_with_that_version(tmp_path: Path) -> None:
    # The release bumps the version but not native_launcher/: a cache hit would ship a
    # launcher whose --version prints the old version (verify_installer_panel fails).
    src = _launcher_src(tmp_path)
    cache = tmp_path / "cache"
    cargo = _FakeCargo()
    old = be.build_launcher(src, app_version="1.2.471", cache_root=cache, cargo="cargo", run=cargo)
    new = be.build_launcher(src, app_version="1.2.472", cache_root=cache, cargo="cargo", run=cargo)
    assert old != new and len(cargo.calls) == 2
    assert [e["DUCKY_APP_VERSION"] for e in cargo.envs] == ["1.2.471", "1.2.472"]
    assert be.build_launcher(src, app_version="1.2.472", cache_root=cache, cargo="cargo", run=cargo) == new
    assert len(cargo.calls) == 2


def test_the_launcher_is_built_after_the_version_bump() -> None:
    text = Path(be.__file__).read_text(encoding="utf-8")
    main = text[text.index("def main("):]
    assert main.index("_bump_package_version(init_py)") < main.index("build_launcher(")


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


def _fails(fn, *args, **kwargs) -> str:
    """The build stops non-zero with the rustup message."""
    with pytest.raises(SystemExit) as stop:
        fn(*args, **kwargs)
    text = str(stop.value.code)
    assert stop.value.code not in (None, 0) and be.RUST_REQUIRED in text
    return text


def test_no_cargo_fails_the_build(tmp_path: Path, monkeypatch) -> None:
    src = _launcher_src(tmp_path)
    monkeypatch.setattr(be.shutil, "which", lambda _name: None)
    assert "cargo not found" in _fails(be.build_launcher, src, cache_root=tmp_path / "cache")
    app = tmp_path / "app"
    app.mkdir()
    _fails(be.place_launcher, None, app)
    assert list(app.iterdir()) == []


def test_failed_cargo_build_fails_the_build(tmp_path: Path) -> None:
    src = _launcher_src(tmp_path)
    cache = tmp_path / "cache"
    assert "rc=101" in _fails(be.build_launcher, src, cache_root=cache, cargo="cargo", run=_FakeCargo(rc=101))
    assert not (cache / be.launcher_source_hash(src)).exists()


def test_cargo_that_cannot_start_fails_the_build(tmp_path: Path) -> None:
    def _oserror(*_a, **_k):
        raise OSError("no such file")

    _fails(be.build_launcher, _launcher_src(tmp_path), cache_root=tmp_path / "c", cargo="cargo", run=_oserror)


def test_missing_source_tree_fails_the_build(tmp_path: Path) -> None:
    _fails(be.build_launcher, tmp_path / "native_launcher", cache_root=tmp_path / "c", run=_FakeCargo())


@pytest.mark.parametrize("flag", ["--allow-no-launcher", "--no-launcher", "--skip-launcher"])
def test_there_is_no_opt_out_flag(monkeypatch, flag: str) -> None:
    monkeypatch.setattr(sys, "argv", ["build_exes.py", flag])
    with pytest.raises(SystemExit) as stop:
        be._parse_args()
    assert stop.value.code == 2  # argparse: unrecognized argument


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


def test_launcher_without_its_dll_fails_the_build(tmp_path: Path) -> None:
    exe = tmp_path / "cached" / be.LAUNCHER_EXE
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    app = tmp_path / "app"
    app.mkdir()
    assert "WebView2Loader.dll" in _fails(be.place_launcher, exe, app)
    assert list(app.iterdir()) == []


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


def test_build_without_the_dll_fails_the_build(tmp_path: Path) -> None:
    src = _launcher_src(tmp_path)
    cache = tmp_path / "cache"
    assert "WebView2Loader.dll" in _fails(be.build_launcher, src, cache_root=cache, cargo="cargo", run=_FakeCargo(dll=False))
    assert not (cache / be.launcher_source_hash(src)).exists()


def test_both_files_present_builds_and_places(tmp_path: Path) -> None:
    exe = be.build_launcher(_launcher_src(tmp_path), cache_root=tmp_path / "cache", cargo="cargo", run=_FakeCargo())
    app = tmp_path / "UEFN-Ducky-1.2.3"
    app.mkdir()
    assert be.place_launcher(exe, app) == app / be.LAUNCHER_EXE
    assert sorted(p.name for p in app.iterdir()) == ["Ducky-Launcher.exe", "WebView2Loader.dll"]


def test_cache_root_is_under_localappdata(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert be.launcher_cache_root() == tmp_path / "UEFN-Ducky" / "build_cache" / "launcher"


# --- Linux build ---------------------------------------------------------------------
BUILD = Path(__file__).resolve().parent
SPEC_TEXT = (BUILD / "unified.spec").read_text(encoding="utf-8")


class _FakeLinuxBuild:
    def __init__(self, rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []
        self.rc = rc

    def __call__(self, cmd, cwd=None, env=None):
        self.calls.append(list(cmd))
        self.envs.append(env)
        if self.rc == 0:
            out = Path(env["CARGO_TARGET_DIR"]) / cmd[-1] / "release"
            out.mkdir(parents=True, exist_ok=True)
            (out / be.LINUX_LAUNCHER).write_bytes(b"\x7fELF launcher")

        class _P:
            returncode = self.rc

        return _P()


def _linux_launcher_src(tmp_path: Path) -> Path:
    src = _launcher_src(tmp_path)
    (src / "linux-build.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    return src


def test_linux_names_keep_the_bridge_stem_the_launcher_looks_for() -> None:
    assert be.linux_names(dev_build=False) == ("uefn-ducky", "uefn-ducky-bridge")
    assert be.linux_names(dev_build=True) == ("uefn-ducky-dev", "uefn-ducky-dev-bridge")
    assert all(name.lower().endswith("-bridge") for name in (be.linux_names(dev_build=False)[1],))


def test_linux_launcher_target_per_cpu() -> None:
    assert be.linux_launcher_target("x86_64") == "x86_64-unknown-linux-gnu"
    assert be.linux_launcher_target("aarch64") == "aarch64-unknown-linux-gnu"
    with pytest.raises(SystemExit):
        be.linux_launcher_target("riscv64")


def test_linux_launcher_builds_once_per_source_and_version(tmp_path: Path) -> None:
    src = _linux_launcher_src(tmp_path)
    run = _FakeLinuxBuild()
    kw = dict(target="x86_64-unknown-linux-gnu", cache_root=tmp_path / "c", run=run)
    first = be.build_linux_launcher(src, app_version="1.2.3", **kw)
    assert first.read_bytes() == b"\x7fELF launcher" and first.name == "Ducky-Launcher"
    assert run.calls[0][0] == "bash" and run.calls[0][1:] == [str(src / "linux-build.sh"), "x86_64-unknown-linux-gnu"]
    assert run.envs[0]["DUCKY_APP_VERSION"] == "1.2.3"
    assert be.build_linux_launcher(src, app_version="1.2.3", **kw) == first and len(run.calls) == 1
    assert be.build_linux_launcher(src, app_version="1.2.4", **kw) != first and len(run.calls) == 2


def test_linux_launcher_failure_stops_the_build(tmp_path: Path) -> None:
    src = _linux_launcher_src(tmp_path)
    assert "rc=1" in _fails(be.build_linux_launcher, src, app_version="1", target="t", cache_root=tmp_path / "c",
                            run=_FakeLinuxBuild(rc=1))
    _fails(be.build_linux_launcher, tmp_path / "none", app_version="1", target="t", cache_root=tmp_path / "c")


def test_linux_cache_root_follows_xdg(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert be.linux_cache_root() == tmp_path / "UEFN-Ducky" / "build_cache" / "launcher"


def test_linux_build_folder_check(tmp_path: Path) -> None:
    app, bridge = be.linux_names(dev_build=False)
    assert "uefn-ducky" in be.missing_linux_files(tmp_path, app, bridge)
    for name in (app, bridge, be.LINUX_LAUNCHER, "_internal/libportaudio.so.2", "_internal/libXss.so.1",
                 *(f"_internal/models/search/{n}" for n in be.SEARCH_MODEL_FILES)):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x")
    # No PyGObject: the window cannot open, so the folder is not done.
    assert be.missing_linux_files(tmp_path, app, bridge) == [
        "_internal/gi/_gi*.so", "_internal/gi_typelibs/WebKit2-4.1.typelib"]
    for name in ("_internal/gi/_gi.cpython-312-x86_64-linux-gnu.so", "_internal/gi_typelibs/WebKit2-4.1.typelib"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"x")
    assert be.missing_linux_files(tmp_path, app, bridge) == []


def test_linux_build_deps_name_the_missing_headers() -> None:
    class Done:
        def __init__(self, rc):
            self.returncode = rc

    assert be.missing_linux_build_deps(which=lambda _: None) == ["pkg-config"]
    have = {"cairo"}
    run = lambda cmd: Done(0 if cmd[-1] in have else 1)  # noqa: E731
    assert be.missing_linux_build_deps(run=run, which=lambda _: "/usr/bin/pkg-config") == ["gobject-introspection-1.0"]
    have.add("gobject-introspection-1.0")
    assert be.missing_linux_build_deps(run=run, which=lambda _: "/usr/bin/pkg-config") == []
    assert "libgirepository1.0-dev" in be.LINUX_BUILD_APT and "gir1.2-webkit2-4.1" in be.LINUX_BUILD_APT
    assert "libxss1" in be.LINUX_BUILD_APT


def test_spec_linux_branch_bundles_libxss_for_tkinter() -> None:
    # libX* stays the host's, but libXss is not on every WebKitGTK host: it must ship.
    assert 'LINUX_XSS = "libXss.so.1"' in SPEC_TEXT
    assert "_linux_bins.append((str(_xss)" in SPEC_TEXT
    assert "if dest in _tk_keep:" in SPEC_TEXT and "_webkit_libs -= _tk_keep" in SPEC_TEXT
    assert "frozen build is missing {LINUX_XSS}" in SPEC_TEXT


def test_linux_freeze_installs_pygobject_below_girepository_2() -> None:
    req = (BUILD / "linux" / "appimage" / "requirements-gi.txt").read_text(encoding="utf-8")
    assert 'PyGObject>=3.42,<3.50; sys_platform == "linux"' in req
    # Not in requirements.txt: installs without the GI headers (linux-tests, Windows) stay as they were.
    lines = (BUILD.parent / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert not [ln for ln in lines if ln.lower().startswith(("pygobject", "pycairo"))]
    text = Path(be.__file__).read_text(encoding="utf-8")
    linux = text[text.index("def main_linux("): text.index("def main() -> int:")]
    assert '"-r", str(gi_req)' in linux and "missing_linux_build_deps()" in linux
    assert "gi_req" not in text[text.index("def main() -> int:"):]


def test_spec_linux_bundles_pygobject_and_keeps_the_host_gtk() -> None:
    for name in ('_linux_hidden += ["gi", "cairo"]', '"WebKit2": "4.1"', '"Gtk": "3.0"', '"Soup": "3.0"',
                 '"libwebkit2gtk-"', '"libglib-2.0."', '"pyi_rth_gtk"', "a.scripts = [e for e in a.scripts"):
        assert name in SPEC_TEXT, name
    # Windows gets the same empty hooksconfig as before.
    assert 'hooksconfig={"gi": {"module-versions": LINUX_GI_VERSIONS' in SPEC_TEXT
    assert "    if LINUX\n    else {}," in SPEC_TEXT
    # The host WebKitGTK's whole ldd closure stays the host's (22.04's libnghttp2 broke 24.04's libsoup).
    assert "_webkit_libs = _ldd(_webkit)" in SPEC_TEXT and "dest in _webkit_libs" in SPEC_TEXT
    # ...and so does whatever the host-side GIO modules / pixbuf loaders link (22.04's gnutls on fedora).
    assert "_webkit_libs |= _ldd(_e[1])" in SPEC_TEXT
    # PyInstaller puts GIO modules (libgiognutls.so, which links gnutls) in gio_modules/.
    assert 'LINUX_HOST_DIRS = ("gio_modules",' in SPEC_TEXT
    # PyInstaller has no WebKit2/JavaScriptCore hooks: ours collect the 4.1 typelibs.
    assert 'hookspath=[str(LINUX_DIR / "pyi_hooks")] if LINUX else [],' in SPEC_TEXT
    hooks = BUILD / "linux" / "appimage" / "pyi_hooks"
    for mod in ("WebKit2", "JavaScriptCore"):
        assert f'GiModuleInfo("{mod}", "4.1")' in (hooks / f"hook-gi.repository.{mod}.py").read_text(encoding="utf-8")
        assert "add_runtime_module" in (hooks / "pre_safe_import_module" / f"hook-gi.repository.{mod}.py").read_text(
            encoding="utf-8")


def test_runtime_smoke_builds_a_webview_on_linux_only() -> None:
    smoke = (BUILD.parent / "ducky_app" / "frontend" / "runtime_smoke.py").read_text(encoding="utf-8")
    check = smoke[smoke.index("def _check_gtk_window"):smoke.index("def check_panel_recovery")]
    for name in ('require_version("Gtk", "3.0")', 'require_version("WebKit2", "4.1")', "WebKit2.WebView()",
                 "import webview.platforms.gtk"):
        assert name in check, name
    assert 'if platform_info.IS_LINUX:\n        report["window"] = _check_gtk_window()' in smoke
    container = (BUILD / "linux" / "appimage" / "smoke_container.sh").read_text(encoding="utf-8")
    assert 'r.get("window")' in container and "GTK cannot be loaded" in container


def test_main_sends_linux_to_its_own_build_and_windows_runs_as_before() -> None:
    text = Path(be.__file__).read_text(encoding="utf-8")
    main = text[text.index("def main() -> int:"):]
    head = main[: main.index("dev_build")]
    assert 'if sys.platform.startswith("linux"):\n        return main_linux(args)' in head
    linux = text[text.index("def main_linux("): text.index("def main() -> int:")]
    # Shared with Windows: the same version file, bump and PyInstaller pin.
    assert "_bump_package_version(init_py)" in linux and "pyinstaller==6.22.3" in main
    assert linux.index("_bump_package_version") < linux.index("build_linux_launcher(")
    assert "pyinstaller==6.22.3" in linux


def test_spec_linux_branch_bundles_sounddevice_and_portaudio() -> None:
    assert 'LINUX_PORTAUDIO = "libportaudio.so.2"' in SPEC_TEXT
    for name in ('"sounddevice"', '"_sounddevice"', '"_sounddevice_data"', '"ptyprocess"', '"keyring.backends"'):
        assert name in SPEC_TEXT, name
    assert "runtime_hooks=_linux_rth" in SPEC_TEXT and "_linux_bins" in SPEC_TEXT
    hook = BUILD / "linux" / "appimage" / "pyi_rth_portaudio.py"
    assert 'name == "portaudio"' in hook.read_text(encoding="utf-8")


def test_spec_windows_parts_stay_on_for_windows() -> None:
    # Windows still requires its ffmpeg zip and winpty; only LINUX skips them.
    assert 'if not LINUX and not (_ffmpeg_bundle / "ffmpeg.exe").is_file():' in SPEC_TEXT
    assert "if not LINUX and not _pty_bins and not _pty_datas:" in SPEC_TEXT
    assert 'LINUX = sys.platform.startswith("linux")' in SPEC_TEXT


def test_portaudio_hook_finds_the_bundled_library(tmp_path: Path, monkeypatch) -> None:
    import ctypes.util
    import runpy

    real = ctypes.util.find_library
    (tmp_path / "libportaudio.so.2").write_bytes(b"\x7fELF")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    try:
        runpy.run_path(str(BUILD / "linux" / "appimage" / "pyi_rth_portaudio.py"))
        assert ctypes.util.find_library("portaudio") == str(tmp_path / "libportaudio.so.2")
    finally:
        ctypes.util.find_library = real
