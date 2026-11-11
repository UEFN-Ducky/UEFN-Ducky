"""A window start drops launcher marks it inherited; a launcher's backend keeps them.

1.2.494 and 1.2.495 pass their whole environment to the version they switch to. Under
Ducky-Launcher.exe that includes UEFN_DUCKY_NATIVE_CHILD=1, so the new version took itself for
a launcher's backend, skipped its own launcher and ran the slow Python window: seen on the
first self-update under the launcher, 1.2.494 to 1.2.495 (Nov 11 2026).
"""

from __future__ import annotations

import os

from frontend import launcher, native_switch
from frontend.app_versions import _LAUNCHER_CHILD_ENV


def test_a_window_start_drops_the_marks_and_its_launcher_starts_again(monkeypatch) -> None:
    for name in _LAUNCHER_CHILD_ENV:
        monkeypatch.setenv(name, "1")
    argv = ["UEFN-Ducky.exe", "--after-update", "4242"]
    assert launcher._drop_stale_launcher_mark(argv) is True
    assert not [name for name in _LAUNCHER_CHILD_ENV if name in os.environ]
    assert launcher._drop_stale_launcher_mark(argv) is False  # nothing left to drop

    started: list[list[str]] = []
    monkeypatch.setattr(native_switch, "take_no_native", lambda: False)
    monkeypatch.setattr(native_switch, "launcher_exe", lambda: type("Exe", (), {"is_file": lambda self: True})())
    monkeypatch.setattr(native_switch, "webview2_ok", lambda: True)
    monkeypatch.setattr(native_switch, "crash_loop_active", lambda: False)
    monkeypatch.setattr(native_switch, "run_native", lambda args, timeout_s, exe=None: started.append(list(args)))
    monkeypatch.setattr(native_switch, "record_start", lambda *a, **k: {})
    assert native_switch.maybe_start_native(argv) is True and started == [argv]


def test_a_launchers_backend_keeps_its_marks(monkeypatch) -> None:
    for name in _LAUNCHER_CHILD_ENV:
        monkeypatch.setenv(name, "1")
    assert launcher._drop_stale_launcher_mark(["UEFN-Ducky.exe", "--backend"]) is False
    assert all(os.environ.get(name) == "1" for name in _LAUNCHER_CHILD_ENV)


def test_with_the_mark_left_in_place_no_launcher_starts(monkeypatch) -> None:
    """What 1.2.495 did after its switch: the reason the marks are dropped."""
    monkeypatch.setenv(native_switch.NATIVE_CHILD_ENV, "1")
    monkeypatch.setattr(native_switch, "take_no_native", lambda: False)
    monkeypatch.setattr(native_switch, "run_native", lambda *a, **k: (_ for _ in ()).throw(AssertionError("started")))
    assert native_switch.maybe_start_native(["UEFN-Ducky.exe", "--after-update", "4242"]) is False
