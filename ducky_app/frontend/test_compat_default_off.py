"""An update that ships Ducky-Launcher.exe keeps every entry point.

Every launch tries the launcher (fallback in test_compat_fallback.py): the ``native_launcher``
setting no longer turns it off, so a saved ``off`` or a bad value starts it too.

Pins what every older version and every Windows entry point relies on: the EXE names, the
Run key, Open with and ``uefn-ducky://`` (names unchanged; the command starts the launcher
when it is present and the setting isn't off), the uninstall key, the panel port, the
panel.pid hand-off, and that every ``native_launcher`` value starts the launcher.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from frontend import autostart, native_switch, open_files, settings
from frontend.app_paths import exe_name_matches, process_name_matches, resolve_app_data_dir
from frontend.frozen_process import is_uefn_ducky_executable, panel_pid_path

LAUNCHER = "Ducky-Launcher.exe"


# --- fixtures ---------------------------------------------------------------------------


class _FakeKey:
    def __init__(self, reg: "_FakeWinreg", path: str) -> None:
        self.reg, self.path = reg, path

    def __enter__(self) -> "_FakeKey":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeWinreg(types.ModuleType):
    """Just enough winreg to see every value a register call would write."""

    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"
    REG_SZ = 1
    KEY_SET_VALUE = 2
    KEY_READ = 4
    KEY_WOW64_64KEY = 8
    KEY_WOW64_32KEY = 16

    def __init__(self) -> None:
        super().__init__("winreg")
        self.values: dict[tuple[str, str], str] = {}

    def CreateKeyEx(self, root, path, *args):  # noqa: N802 — winreg's names
        return _FakeKey(self, path)

    def OpenKey(self, root, path, *args):  # noqa: N802
        if not any(p == path for p, _ in self.values):
            raise OSError("missing")
        return _FakeKey(self, path)

    def SetValueEx(self, key, name, _reserved, _kind, value):  # noqa: N802
        self.values[(key.path, name)] = value

    def QueryValueEx(self, key, name):  # noqa: N802
        try:
            return self.values[(key.path, name)], 1
        except KeyError:
            raise OSError("missing") from None

    def DeleteValue(self, key, name):  # noqa: N802
        self.values.pop((key.path, name), None)


@pytest.fixture
def fake_winreg(monkeypatch: pytest.MonkeyPatch) -> _FakeWinreg:
    reg = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    monkeypatch.setattr(sys, "platform", "win32")
    return reg


@pytest.fixture
def install_dir(tmp_path: Path) -> Path:
    """An install folder after an update: UEFN-Ducky.exe with Ducky-Launcher.exe beside it."""
    folder = tmp_path / "Programs" / "UEFN Ducky"
    folder.mkdir(parents=True)
    (folder / "UEFN-Ducky.exe").write_bytes(b"MZ")
    (folder / LAUNCHER).write_bytes(b"MZ")
    return folder


def _mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(
        settings.PanelSettings, "load", classmethod(lambda cls, **_: settings.PanelSettings(native_launcher=mode))
    )


@pytest.fixture
def files_backend(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
    path = resolve_app_data_dir() / "panel_settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# --- 1. EXE names -----------------------------------------------------------------------


def test_exe_names_are_pinned() -> None:
    assert native_switch.LAUNCHER_EXE_NAME == LAUNCHER
    for name in ("UEFN-Ducky.exe", "UEFN-Ducky-1.2.470.exe", "UEFN-Ducky-Bridge.exe"):
        assert exe_name_matches(name) and is_uefn_ducky_executable(name)
    # Old versions sweep UEFN-Ducky*: the launcher's name must never match them.
    assert not exe_name_matches(LAUNCHER)
    assert not is_uefn_ducky_executable(Path("C:/x") / LAUNCHER)
    assert not process_name_matches("Ducky-Launcher")
    assert not exe_name_matches("UEFN-Ducky-Setup-1.2.470.exe")


def test_launcher_is_looked_up_next_to_the_app_exe(monkeypatch: pytest.MonkeyPatch, install_dir: Path) -> None:
    monkeypatch.delenv(native_switch.LAUNCHER_EXE_ENV, raising=False)
    monkeypatch.setattr(sys, "executable", str(install_dir / "UEFN-Ducky.exe"))
    assert native_switch.launcher_exe() == (install_dir / LAUNCHER).resolve()


# --- 2. Run 'UEFN Ducky' ----------------------------------------------------------------


def test_run_key_and_command_are_pinned(install_dir: Path) -> None:
    assert autostart.RUN_KEY == r"Software\Microsoft\Windows\CurrentVersion\Run"
    assert autostart.VALUE_NAME == "UEFN Ducky"
    exe = install_dir / "UEFN-Ducky.exe"
    assert autostart.command_for(exe) == f'"{exe}" --background'


def _packaged(monkeypatch: pytest.MonkeyPatch, stable: Path) -> None:
    import frontend.app_versions as app_versions
    import frontend.bundle_root as bundle_root

    monkeypatch.delenv("DUCKY_SANDBOX", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(bundle_root, "is_packaged_runtime", lambda: True)
    monkeypatch.setattr(app_versions, "stable_exe", lambda *a, **k: stable)


def test_run_key_off_names_uefn_ducky_exe(
    monkeypatch: pytest.MonkeyPatch, fake_winreg: _FakeWinreg, install_dir: Path
) -> None:
    _packaged(monkeypatch, install_dir / "UEFN-Ducky.exe")
    _mode(monkeypatch, "off")
    assert autostart.apply(True) is True
    command = fake_winreg.values[(autostart.RUN_KEY, "UEFN Ducky")]
    assert command == f'"{(install_dir / "UEFN-Ducky.exe").resolve()}" --background'
    assert LAUNCHER not in command


@pytest.mark.parametrize("mode", ["on", "auto"])
def test_run_key_on_starts_the_launcher_under_the_same_name(
    monkeypatch: pytest.MonkeyPatch, fake_winreg: _FakeWinreg, install_dir: Path, mode: str
) -> None:
    _packaged(monkeypatch, install_dir / "UEFN-Ducky.exe")
    _mode(monkeypatch, mode)
    assert autostart.apply(True) is True
    assert fake_winreg.values == {
        (autostart.RUN_KEY, "UEFN Ducky"): f'"{(install_dir / LAUNCHER).resolve()}" --background'
    }


def test_run_key_is_left_alone_when_stable_exe_is_the_launcher(
    monkeypatch: pytest.MonkeyPatch, fake_winreg: _FakeWinreg, install_dir: Path
) -> None:
    # Even if something resolved the stable EXE to the launcher, the entry is left alone.
    fake_winreg.values.clear()
    _packaged(monkeypatch, install_dir / LAUNCHER)
    assert autostart.managed_exe() is None
    assert autostart.apply(True) is False
    assert fake_winreg.values == {}


# --- 3. Open with and uefn-ducky:// -----------------------------------------------------


@pytest.mark.parametrize(("mode", "target"), [("off", "UEFN-Ducky.exe"), ("on", LAUNCHER), ("auto", LAUNCHER)])
def test_open_with_keeps_uefn_ducky_exe_names(
    monkeypatch: pytest.MonkeyPatch, fake_winreg: _FakeWinreg, install_dir: Path, mode: str, target: str
) -> None:
    _mode(monkeypatch, mode)
    exe = (install_dir / "UEFN-Ducky.exe").resolve()
    open_files.register_windows_open_with(exe)
    app_key = r"Software\Classes\Applications\UEFN-Ducky.exe"
    assert fake_winreg.values[(rf"{app_key}\shell\open\command", "")] == f'"{exe.parent / target}" "%1"'
    assert fake_winreg.values[(rf"{app_key}\DefaultIcon", "")] == str(exe)
    assert fake_winreg.values[(app_key, "FriendlyAppName")] == "UEFN Ducky"
    assert fake_winreg.values[(rf"{app_key}\SupportedTypes", ".*")] == ""
    assert (r"Software\Classes\*\OpenWithList\UEFN-Ducky.exe", "") in fake_winreg.values


@pytest.mark.parametrize(("mode", "target"), [("off", "UEFN-Ducky.exe"), ("on", LAUNCHER), ("auto", LAUNCHER)])
def test_url_protocol_keeps_the_uefn_ducky_key(
    monkeypatch: pytest.MonkeyPatch, fake_winreg: _FakeWinreg, install_dir: Path, mode: str, target: str
) -> None:
    _mode(monkeypatch, mode)
    exe = (install_dir / "UEFN-Ducky.exe").resolve()
    assert open_files.DEEP_LINK_PREFIX == "uefn-ducky://"
    open_files.register_windows_url_protocol(exe)
    key = r"Software\Classes\uefn-ducky"
    assert fake_winreg.values[(key, "")] == "URL:UEFN Ducky"
    assert fake_winreg.values[(key, "URL Protocol")] == ""
    assert fake_winreg.values[(rf"{key}\shell\open\command", "")] == f'"{exe.parent / target}" "%1"'


def test_launcher_exe_is_never_registered(fake_winreg: _FakeWinreg, install_dir: Path) -> None:
    open_files.register_windows_open_with(install_dir / LAUNCHER)
    open_files.register_windows_url_protocol(install_dir / LAUNCHER)
    assert fake_winreg.values == {}


def test_deep_links_and_paths_still_parse_from_the_same_argv(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    argv = ["UEFN-Ducky.exe", "uefn-ducky://store/install?id=x", str(f)]
    assert open_files.cli_deep_links(argv) == ["uefn-ducky://store/install?id=x"]
    assert [Path(p).resolve() for p in open_files.cli_open_paths(argv)] == [f.resolve()]


# --- 4. Uninstall keys ------------------------------------------------------------------


def test_uninstall_key_is_pinned() -> None:
    from frontend.install_info import INNO_APP_ID, UNINSTALL_KEY

    assert INNO_APP_ID == "{EAD694ED-E221-40B0-909B-AFFD7F683C9E}"
    assert UNINSTALL_KEY == (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{EAD694ED-E221-40B0-909B-AFFD7F683C9E}_is1"
    )


# --- 5. pick_panel_port -----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["off", "on", "auto", None])
def test_pick_panel_port_ignores_the_setting(
    monkeypatch: pytest.MonkeyPatch, files_backend: Path, mode: str | None
) -> None:
    if mode is not None:
        files_backend.write_text(json.dumps({"native_launcher": mode}), encoding="utf-8")
    monkeypatch.setenv("UEFN_DUCKY_PANEL_PORT", "")
    monkeypatch.delenv("UEFN_DUCKY_PANEL_PORT")
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", settings.DEFAULT_PANEL_LISTENER_PORT)
    monkeypatch.setattr(settings, "_port_reserved", lambda port: False)
    assert settings.DEFAULT_PANEL_LISTENER_PORT == 4200
    assert settings.PANEL_PORT_FILE == "panel_port.json"
    assert settings.pick_panel_port() == 4200
    assert not (resolve_app_data_dir() / settings.PANEL_PORT_FILE).exists()
    assert "UEFN_DUCKY_PANEL_PORT" not in os.environ


# --- 6. panel.pid hand-off --------------------------------------------------------------


def test_panel_pid_path_and_handoff_read() -> None:
    path = panel_pid_path(for_write=True)
    assert path == resolve_app_data_dir() / "panel.pid"
    assert open_files.read_living_panel_pid() is None
    path.write_text(str(os.getpid()), encoding="utf-8")
    assert open_files.read_living_panel_pid() == os.getpid()
    path.write_text("not a pid", encoding="utf-8")
    assert open_files.read_living_panel_pid() is None
    # Nothing launcher-specific sits next to it: a hand-off still finds the same file.
    assert not (resolve_app_data_dir() / "launcher").exists()


# --- 7. the default is auto; every value starts Ducky-Launcher.exe -----------------------
# There is no turning the launcher off (its window is up in ~0.1 s, the Python window in
# 1-2 s): a saved off or a bad value still starts it. Python only when it can't run.


@pytest.fixture
def launcher_ready(monkeypatch: pytest.MonkeyPatch, install_dir: Path) -> list[object]:
    """The launcher is present and paints: each start is recorded here instead of spawned."""
    started: list[object] = []
    exe = install_dir / LAUNCHER
    exe.write_bytes(b"MZ")
    monkeypatch.setenv(native_switch.LAUNCHER_EXE_ENV, str(exe))
    monkeypatch.delenv(native_switch.NATIVE_CHILD_ENV, raising=False)
    monkeypatch.setattr(native_switch, "webview2_ok", lambda: True)
    monkeypatch.setattr(native_switch, "run_native", lambda argv, timeout_s, exe=None: started.append(argv))
    monkeypatch.setattr(native_switch, "record_start", lambda *a: None)
    monkeypatch.setattr(native_switch, "record_start_soon", lambda *a: None)
    return started


ARGV = ["UEFN-Ducky.exe", "uefn-ducky://store/install?id=x"]


def test_the_default_is_auto() -> None:
    assert settings.PanelSettings().native_launcher == "auto"


@pytest.mark.parametrize("raw", [None, "missing"])
def test_absent_key_in_the_file_loads_as_auto(files_backend: Path, raw: object) -> None:
    data: dict[str, object] = {"web_access": "on"}
    if raw is None:
        data["native_launcher"] = None  # a null is dropped by the loader: same as missing
    files_backend.write_text(json.dumps(data), encoding="utf-8")
    assert settings.PanelSettings.load().native_launcher == "auto"


def test_absent_key_in_the_db_loads_as_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DUCKY_STORE_BACKEND", raising=False)
    monkeypatch.delenv("DUCKY_STORE_BACKEND_SETTINGS", raising=False)
    assert settings.PanelSettings.load().native_launcher == "auto"


def test_saved_off_survives_a_reload(files_backend: Path) -> None:
    s = settings.PanelSettings.load()
    s.native_launcher = "off"
    s.save()
    assert settings.PanelSettings.load().native_launcher == "off"


@pytest.mark.parametrize("raw", ["off", "OFF", "garbage", "", 0])
def test_off_or_bad_value_still_starts_the_launcher(
    files_backend: Path, launcher_ready: list[object], raw: object
) -> None:
    files_backend.write_text(json.dumps({"web_access": "on", "native_launcher": raw}), encoding="utf-8")
    assert native_switch.maybe_start_native(list(ARGV)) is True
    assert launcher_ready == [list(ARGV)]


def test_decide_is_python_only_when_the_launcher_cannot_run() -> None:
    for setting in ("off", "auto", "on"):
        for held in (True, False):
            assert native_switch.decide(setting, True, True, held) == "native"
            assert native_switch.decide(setting, False, True, held) == "python"
            assert native_switch.decide(setting, True, False, held) == "python"


def test_launcher_py_runs_the_hook_before_the_python_window() -> None:
    """The only launch hook is native_switch.maybe_start_native, after the hand-off to a running
    Ducky and before the Python window; it is wrapped so any error runs the Python window."""
    src = (Path(__file__).resolve().parent / "launcher.py").read_text(encoding="utf-8")
    main = src[src.index("def main()"):]
    hook = main.index("maybe_start_native(sys.argv, start_hidden=start_hidden)")
    assert main.index("pick_panel_port()") < main.index("_hand_off_to_newest(bridge=False)") < hook
    assert hook < main.index("boot_report.begin(") < main.index("from frontend.ui_web.run import run")
    block = main[main.rindex("try:", 0, hook):main.index("raise SystemExit(0)", hook)]
    assert "except Exception:" in block and "native = False" in block
    # No other spawn of the launcher anywhere in launcher.py.
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    assert LAUNCHER not in code and "run_native" not in code and "Popen" not in code
