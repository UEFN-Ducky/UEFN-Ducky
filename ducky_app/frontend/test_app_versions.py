"""Every start lands on the newest downloaded version that works; saved paths stay put."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from frontend import app_versions as av


def _version(version: str, *, ready: bool = True) -> Path:
    folder = av.version_dir(version)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "UEFN-Ducky.exe").write_bytes(b"MZ")
    (folder / "UEFN-Ducky-Bridge.exe").write_bytes(b"MZ")
    if ready:
        (folder / av.READY_MARKER).write_text("{}", encoding="utf-8")
    return folder


def test_newest_ready_version_wins_and_bad_or_unfinished_ones_are_skipped() -> None:
    _version("1.2.410")
    _version("1.2.412")
    _version("1.2.413", ready=False)
    (av.versions_dir() / "app-1.2.414.partial").mkdir()
    assert [v for v, _ in av.ready_versions()] == ["1.2.412", "1.2.410"]
    assert av.newest_usable("1.2.409")[0] == "1.2.412"
    assert av.newest_usable("1.2.412") is None
    av.mark_bad("1.2.412", "crashed")
    assert av.newest_usable("1.2.409")[0] == "1.2.410"
    assert av.newest_usable("1.2.410") is None


def test_a_version_that_never_reaches_ready_turns_bad() -> None:
    _version("1.2.411")
    assert av.record_start("1.2.411") is True
    av.mark_ready("1.2.411")
    assert av.record_start("1.2.411") is True
    assert av.record_start("1.2.411") is True
    # Two starts without ready: the third is refused and the version is bad.
    assert av.record_start("1.2.411") is False
    assert av.is_bad("1.2.411")
    assert av.newest_usable("1.2.409") is None


def test_saved_paths_name_the_started_exe(monkeypatch, tmp_path) -> None:
    installed = tmp_path / "Programs" / "UEFN Ducky" / "UEFN-Ducky.exe"
    monkeypatch.setenv(av.LAUNCHER_ENV, str(installed))
    assert av.launcher_exe() == installed
    assert av.stable_exe("UEFN-Ducky-Bridge.exe") == installed.with_name("UEFN-Ducky-Bridge.exe")
    assert av.hand_off_env()[av.LAUNCHER_ENV] == str(installed)
    monkeypatch.delenv(av.LAUNCHER_ENV)
    assert av.launcher_exe() == Path(sys.executable)


def test_a_hand_off_lets_the_newer_version_pick_its_window(monkeypatch) -> None:
    """1.2.475's root launcher ran the Python window with UEFN_DUCKY_NO_NATIVE=1; handed on,
    it kept the updated version off the fast launcher. The newer version decides for itself."""
    monkeypatch.setenv("UEFN_DUCKY_NO_NATIVE", "1")
    env = av.hand_off_env()
    assert "UEFN_DUCKY_NO_NATIVE" not in env
    assert os.environ["UEFN_DUCKY_NO_NATIVE"] == "1"  # this process keeps its own


def _launcher_dirs(tmp_path: Path) -> tuple[Path, Path]:
    new, root = tmp_path / "versions" / "app-1.2.478", tmp_path / "Programs" / "UEFN Ducky"
    new.mkdir(parents=True)
    root.mkdir(parents=True)
    (new / "Ducky-Launcher.exe").write_bytes(b"MZ new launcher")
    (new / "WebView2Loader.dll").write_bytes(b"dll")
    (root / "Ducky-Launcher.exe").write_bytes(b"MZ 1.2.475 launcher")
    (root / "WebView2Loader.dll").write_bytes(b"dll")
    return new, root


def test_the_root_launcher_becomes_the_newer_versions(tmp_path) -> None:
    """Shortcuts start the install's root launcher, which updates never touched: 1.2.475's
    kept every updated Ducky in the slow Python window."""
    new, root = _launcher_dirs(tmp_path)
    assert av.copy_launcher_files(new, root) == ["Ducky-Launcher.exe"]  # the same DLL stays
    assert (root / "Ducky-Launcher.exe").read_bytes() == b"MZ new launcher"
    assert av.copy_launcher_files(new, root) == []  # already current: nothing written
    assert sorted(p.name for p in root.iterdir()) == ["Ducky-Launcher.exe", "WebView2Loader.dll"]


def test_a_running_root_launcher_is_renamed_aside_then_cleaned(tmp_path, monkeypatch) -> None:
    new, root = _launcher_dirs(tmp_path)
    real = os.replace
    calls: list[tuple[str, str]] = []

    def locked_once(src, dst):
        calls.append((Path(src).name, Path(dst).name))
        if len(calls) == 1:
            raise PermissionError("in use")
        return real(src, dst)

    monkeypatch.setattr(av.os, "replace", locked_once)
    assert av.copy_launcher_files(new, root) == ["Ducky-Launcher.exe"]
    assert (root / "Ducky-Launcher.exe").read_bytes() == b"MZ new launcher"
    assert (root / "Ducky-Launcher.exe.old").read_bytes() == b"MZ 1.2.475 launcher"
    monkeypatch.setattr(av.os, "replace", real)
    av.copy_launcher_files(new, root)
    assert not (root / "Ducky-Launcher.exe.old").exists()


def test_a_bad_copy_or_a_bad_source_never_replaces_the_root_launcher(tmp_path, monkeypatch) -> None:
    """A copy another process cut short (or zeros) was renamed over the root launcher, and
    every start from shortcuts and the Run key said "This app can't run on your PC"."""
    new, root = _launcher_dirs(tmp_path)
    real_copy = av.shutil.copy2
    monkeypatch.setattr(av.shutil, "copy2", lambda s, d: Path(d).write_bytes(b"\0" * 15))
    assert av.copy_launcher_files(new, root) == []
    assert (root / "Ducky-Launcher.exe").read_bytes() == b"MZ 1.2.475 launcher"
    assert sorted(p.name for p in root.iterdir()) == ["Ducky-Launcher.exe", "WebView2Loader.dll"]
    monkeypatch.setattr(av.shutil, "copy2", real_copy)
    (new / "Ducky-Launcher.exe").write_bytes(b"\0" * 15)  # a broken version folder
    assert av.copy_launcher_files(new, root) == []
    assert (root / "Ducky-Launcher.exe").read_bytes() == b"MZ 1.2.475 launcher"


def test_agent_hosts_only_clear_their_start_count(monkeypatch) -> None:
    """One host per agent turn ran mark_ready: all of them rewrote the root launcher at once,
    and an older version's host repointed current.json back to itself."""
    from frontend import launcher

    calls: list[str] = []
    monkeypatch.setattr(av, "refresh_root_launcher", lambda: calls.append("refresh"))
    monkeypatch.setattr(av, "write_current", lambda *a, **k: calls.append("current"))
    monkeypatch.setattr(av, "clear_starts", lambda v: calls.append(f"clear {v}"))
    launcher._host_started_fine()
    assert len(calls) == 1 and calls[0].startswith("clear ")


def test_the_root_launcher_is_never_added_or_touched_outside_a_frozen_version(tmp_path, monkeypatch) -> None:
    new, root = _launcher_dirs(tmp_path)
    (root / "Ducky-Launcher.exe").unlink()
    assert av.copy_launcher_files(new, root) == []  # an install without one never gets one
    assert not (root / "Ducky-Launcher.exe").exists()
    called: list[object] = []
    monkeypatch.setattr(av, "copy_launcher_files", lambda *a: called.append(a))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    av.refresh_root_launcher()  # dev run
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv(av.NO_HOP_ENV, "1")
    av.refresh_root_launcher()  # sandboxed test build
    assert called == []


def test_a_version_started_directly_names_the_install(monkeypatch, tmp_path) -> None:
    """A Run key pinned in versions/ (or a launcher hop with no env) started 1.2.472 there:
    it called itself portable and never updated. The install is the start."""
    from frontend import install_info

    monkeypatch.delenv(av.LAUNCHER_ENV, raising=False)
    monkeypatch.setattr(sys, "executable", str(_version("1.2.472") / "UEFN-Ducky.exe"))
    root = tmp_path / "Programs" / "UEFN Ducky"
    monkeypatch.setattr(install_info, "installed_location", lambda: str(root))
    monkeypatch.setattr(av, "apart_from_the_install", lambda: False)  # the install's own copy
    # No install on disk: the version's own EXE, as before.
    assert av.launcher_exe() == Path(sys.executable)
    root.mkdir(parents=True)
    (root / "UEFN-Ducky.exe").write_bytes(b"MZ")
    assert av.launcher_exe() == root / "UEFN-Ducky.exe"
    assert av.stable_exe("Ducky-Launcher.exe") == root / "Ducky-Launcher.exe"
    # An EXE outside versions/ (a portable copy beside an install) stays itself.
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Downloads" / "UEFN-Ducky.exe"))
    assert av.launcher_exe() == tmp_path / "Downloads" / "UEFN-Ducky.exe"


def test_a_sandboxed_copy_never_starts_or_writes_the_real_install(monkeypatch, tmp_path) -> None:
    """A test copy has its own version folders, but the registry still names the person's
    install: a sandboxed copy that updated itself would have put its launcher there."""
    from frontend import app_paths, install_info

    root = tmp_path / "Programs" / "UEFN Ducky"
    root.mkdir(parents=True)
    (root / "UEFN-Ducky.exe").write_bytes(b"MZ")
    (root / "Ducky-Launcher.exe").write_bytes(b"MZ the person's own launcher")
    monkeypatch.setattr(install_info, "installed_location", lambda: str(root))
    monkeypatch.delenv(av.LAUNCHER_ENV, raising=False)
    monkeypatch.delenv(av.NO_HOP_ENV, raising=False)
    version = _version("1.2.496")
    (version / "Ducky-Launcher.exe").write_bytes(b"MZ a test build's launcher")
    monkeypatch.setattr(sys, "executable", str(version / "UEFN-Ducky.exe"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    for sandbox, profile in (("1", ""), ("", "LOCALAPPDATA is not this Windows user's own folder")):
        monkeypatch.setenv("DUCKY_SANDBOX", sandbox) if sandbox else monkeypatch.delenv("DUCKY_SANDBOX", raising=False)
        monkeypatch.setattr(app_paths, "foreign_profile_reason", lambda profile=profile: profile)
        assert av.apart_from_the_install() is True
        assert av.launcher_exe() == Path(sys.executable)
        av.refresh_root_launcher()
        assert (root / "Ducky-Launcher.exe").read_bytes() == b"MZ the person's own launcher"

    # The person's own install (their profile, no test mark): its root launcher follows the version.
    monkeypatch.delenv("DUCKY_SANDBOX", raising=False)
    monkeypatch.setattr(app_paths, "foreign_profile_reason", lambda: "")
    assert av.apart_from_the_install() is False
    assert av.launcher_exe() == root / "UEFN-Ducky.exe"
    av.refresh_root_launcher()
    assert (root / "Ducky-Launcher.exe").read_bytes() == b"MZ a test build's launcher"


def test_handover_is_read_once_and_only_while_fresh() -> None:
    av.write_handover({"hidden": True, "maximized": False})
    got = av.take_handover()
    assert got["hidden"] is True
    assert av.take_handover() == {}
    path = av.versions_dir() / av.HANDOVER_FILE
    path.write_text(json.dumps({"hidden": True, "at": time.time() - 3600}), encoding="utf-8")
    assert av.take_handover() == {}


def test_hand_off_does_nothing_from_source_or_when_turned_off(monkeypatch) -> None:
    _version("9.9.9")
    started = []
    monkeypatch.setattr(av, "start_detached", lambda *a, **k: started.append(a) or True)
    monkeypatch.delattr(sys, "frozen", raising=False)
    av.hand_off("1.2.409", bridge=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv(av.NO_HOP_ENV, "1")
    av.hand_off("1.2.409", bridge=False)
    assert started == []


def test_panel_hands_off_to_the_newest_and_leaves(monkeypatch) -> None:
    folder = _version("1.2.412")
    started: list[tuple] = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv(av.NO_HOP_ENV, raising=False)
    monkeypatch.setattr(sys, "executable", str(Path("C:/Programs/UEFN Ducky/UEFN-Ducky.exe")))
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky.exe", "uefn-ducky://store/x"])
    monkeypatch.setattr(av, "start_detached", lambda exe, args, **k: started.append((exe, args)) or True)

    class Left(Exception):
        pass

    def _exit(code):
        raise Left(code)

    monkeypatch.setattr(av.os, "_exit", _exit)
    try:
        av.hand_off("1.2.410", bridge=False)
    except Left:
        pass
    assert started == [(folder / "UEFN-Ducky.exe", ["uefn-ducky://store/x"])]
    assert av.read_boot()["1.2.412"]["starts"] == 1


def test_a_start_hand_off_leaves_the_last_runs_crash_flag_alone(monkeypatch) -> None:
    # The hop runs before app_opened: marking the run closed here would hide a real crash
    # of the run before from the version it hands to.
    from frontend import usage_stats

    _version("1.2.412")
    touched: list = []
    monkeypatch.setattr(usage_stats, "app_closing", lambda: touched.append("app_closing"))
    monkeypatch.setattr(usage_stats, "_meta_set", lambda k, v: touched.append((k, v)))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv(av.NO_HOP_ENV, raising=False)
    monkeypatch.setattr(sys, "executable", str(Path("C:/Programs/UEFN Ducky/UEFN-Ducky.exe")))
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky.exe"])
    monkeypatch.setattr(av, "start_detached", lambda exe, args, **k: True)
    left: list = []
    monkeypatch.setattr(av.os, "_exit", lambda code: left.append(code))
    av.hand_off("1.2.410", bridge=False)
    assert left == [0] and touched == []


def test_bridge_runs_the_newest_on_its_own_stdio(monkeypatch) -> None:
    folder = _version("1.2.412")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv(av.NO_HOP_ENV, raising=False)
    monkeypatch.setattr(sys, "executable", str(Path("C:/Programs/UEFN Ducky/UEFN-Ducky-Bridge.exe")))
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky-Bridge.exe", "bridge", "--port", "4200"])
    ran: list[tuple] = []
    monkeypatch.setattr(av, "run_attached", lambda exe, args: ran.append((exe, args)) or 7)
    try:
        av.hand_off("1.2.410", bridge=True)
    except SystemExit as exc:
        assert exc.code == 7
    assert ran == [(folder / "UEFN-Ducky-Bridge.exe", ["bridge", "--port", "4200"])]
    # Bridges start all the time: they never count against a version.
    assert "1.2.412" not in av.read_boot()


def test_a_crashed_download_falls_back_but_the_installed_copy_does_not(monkeypatch, tmp_path) -> None:
    folder = _version("1.2.412")
    launcher = tmp_path / "UEFN-Ducky.exe"
    launcher.write_bytes(b"MZ")
    started = []
    monkeypatch.setattr(av, "start_detached", lambda exe, args, **k: started.append(exe) or True)
    monkeypatch.setenv(av.LAUNCHER_ENV, str(launcher))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "elsewhere" / "UEFN-Ducky.exe"))
    assert av.fall_back_after_failed_start("1.2.412", "boom") is False
    monkeypatch.setattr(sys, "executable", str(folder / "UEFN-Ducky.exe"))
    assert av.fall_back_after_failed_start("1.2.412", "boom") is True
    assert started == [launcher]
    assert av.is_bad("1.2.412")
