"""Start with Windows: the Run entry, and who may touch it."""
from __future__ import annotations

import sys

import pytest

from frontend import autostart


class FakeReg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, sub, *args):  # noqa: N802 - winreg's names
        assert (root, sub) == ("HKCU", autostart.RUN_KEY)
        return self._Key()

    def CreateKeyEx(self, root, sub, *args):  # noqa: N802
        assert (root, sub) == ("HKCU", autostart.RUN_KEY)
        return self._Key()

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], 1

    def SetValueEx(self, key, name, _reserved, _kind, value):  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, key, name):  # noqa: N802
        del self.values[name]


@pytest.fixture
def reg(monkeypatch, tmp_path):
    fake = FakeReg()
    exe = tmp_path / "UEFN-Ducky.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(autostart, "_winreg", lambda: fake)
    monkeypatch.setattr(autostart, "managed_exe", lambda: exe)
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    fake.exe = exe  # type: ignore[attr-defined]
    return fake


def test_on_writes_the_background_command(reg):
    assert autostart.apply(True) is True
    assert reg.values[autostart.VALUE_NAME] == f'"{reg.exe}" --background'


def test_off_removes_it_and_is_quiet_when_already_gone(reg):
    autostart.apply(True)
    assert autostart.apply(False) is True
    assert autostart.VALUE_NAME not in reg.values
    assert autostart.apply(False) is True


def test_a_moved_exe_rewrites_the_entry(reg, tmp_path):
    reg.values[autostart.VALUE_NAME] = '"C:\\old\\UEFN-Ducky.exe" --background'
    assert autostart.apply(True) is True
    assert reg.values[autostart.VALUE_NAME] == f'"{reg.exe}" --background'


def test_a_test_copy_never_touches_the_entry(monkeypatch):
    monkeypatch.setenv("DUCKY_SANDBOX", "C:/box")
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    assert autostart.managed_exe() is None
    touched: list[str] = []
    monkeypatch.setattr(autostart, "_winreg", lambda: touched.append("x"))
    assert autostart.apply(True) is False
    assert autostart.apply(False) is False
    assert touched == []


def test_source_checkout_never_touches_the_entry(monkeypatch):
    monkeypatch.delenv("DUCKY_SANDBOX", raising=False)
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setattr("frontend.bundle_root.is_packaged_runtime", lambda: False)
    assert autostart.managed_exe() is None


def test_setup_or_foreign_exe_is_refused(monkeypatch, tmp_path):
    monkeypatch.delenv("DUCKY_SANDBOX", raising=False)
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setattr("frontend.bundle_root.is_packaged_runtime", lambda: True)
    for name in ("UEFN-Ducky-Setup-1.2.471.exe", "python.exe"):
        exe = tmp_path / name
        exe.write_bytes(b"MZ")
        monkeypatch.setattr("frontend.app_versions.stable_exe", lambda exe=exe: exe)
        assert autostart.managed_exe() is None


def test_background_flag_is_taken_out_of_argv():
    argv = ["UEFN-Ducky.exe", "--background", "C:/a.verse"]
    assert autostart.take_background_flag(argv) is True
    assert argv == ["UEFN-Ducky.exe", "C:/a.verse"]
    assert autostart.take_background_flag(argv) is False


def test_setting_is_on_by_default():
    from frontend.settings import PanelSettings

    assert PanelSettings().start_with_windows is True


def _launcher_mode(monkeypatch, mode):
    from frontend.settings import PanelSettings

    monkeypatch.setattr(PanelSettings, "load", classmethod(lambda cls, **_: PanelSettings(native_launcher=mode)))


@pytest.mark.parametrize("mode", ["on", "auto", "off", "garbage"])
def test_run_key_starts_the_stub_even_with_a_launcher(monkeypatch, reg, mode):
    """Sign-in starts UEFN-Ducky.exe: it repairs a broken root launcher before anyone clicks
    it (Nov 7-10 2026: 19 "This app can't run on your PC" starts). Open with keeps the launcher."""
    launcher = reg.exe.parent / "Ducky-Launcher.exe"
    launcher.write_bytes(b"MZ")
    _launcher_mode(monkeypatch, mode)
    assert autostart.start_exe(reg.exe) == launcher
    assert autostart.apply(True) is True
    assert reg.values[autostart.VALUE_NAME] == f'"{reg.exe}" --background'


def test_run_key_stays_on_the_stub_without_a_launcher(monkeypatch, reg):
    _launcher_mode(monkeypatch, "on")
    assert autostart.start_exe(reg.exe) == reg.exe
    assert autostart.apply(True) is True
    assert reg.values[autostart.VALUE_NAME] == f'"{reg.exe}" --background'


def test_a_zero_filled_launcher_is_never_what_open_with_starts(monkeypatch, reg):
    launcher = reg.exe.parent / "Ducky-Launcher.exe"
    launcher.write_bytes(b"\0" * 64)
    assert autostart.start_exe(reg.exe) == reg.exe
    launcher.write_bytes(b"MZ")
    assert autostart.start_exe(reg.exe) == launcher


def test_an_unreadable_setting_still_starts_the_launcher(monkeypatch, reg):
    from frontend.settings import PanelSettings

    launcher = reg.exe.parent / "Ducky-Launcher.exe"
    launcher.write_bytes(b"MZ")

    def boom(cls, **_):
        raise OSError("locked")

    monkeypatch.setattr(PanelSettings, "load", classmethod(boom))
    assert autostart.start_exe(reg.exe) == launcher


def test_self_heal_moves_a_launcher_run_key_back_to_the_stub(monkeypatch, reg):
    launcher = reg.exe.parent / "Ducky-Launcher.exe"
    reg.values[autostart.VALUE_NAME] = f'"{launcher}" --background'  # 1.2.477-1.2.492
    launcher.write_bytes(b"MZ")
    assert autostart.apply(True) is True
    assert reg.values[autostart.VALUE_NAME] == f'"{reg.exe}" --background'
    assert list(reg.values) == [autostart.VALUE_NAME]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_apply_saved_setting_follows_the_setting(monkeypatch, reg):
    from frontend.settings import PanelSettings

    monkeypatch.setattr(PanelSettings, "load", classmethod(lambda cls, **_: PanelSettings(start_with_windows=False)))
    reg.values[autostart.VALUE_NAME] = "x"
    assert autostart.apply_saved_setting() is True
    assert autostart.VALUE_NAME not in reg.values
