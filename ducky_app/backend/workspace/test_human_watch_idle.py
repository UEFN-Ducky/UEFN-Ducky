"""Linux idle time: Mutter first, then org.freedesktop.ScreenSaver, then XScreenSaver."""

from __future__ import annotations

import sys

import pytest

from backend.workspace import human_watch as hw


@pytest.fixture(autouse=True)
def linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")


def _bus(replies: dict[str, tuple[int, str]]):
    """A fake session bus: the reply for the first destination named in *replies*."""
    calls: list[str] = []

    def run(args: list[str]) -> tuple[int, str]:
        dest = args[args.index("--dest") + 1]
        calls.append(dest)
        return replies.get(dest, (1, "Error: org.freedesktop.DBus.Error.ServiceUnknown"))

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _no_x11():
    return None


def test_mutter_reply_in_milliseconds() -> None:
    run = _bus({"org.gnome.Mutter.IdleMonitor": (0, "(uint64 600000,)\n")})
    assert hw.idle_seconds(run=run, x11=_no_x11) == 600.0
    assert run.calls == ["org.gnome.Mutter.IdleMonitor"]


def test_ten_minutes_idle_counts_as_away() -> None:
    away = _bus({"org.gnome.Mutter.IdleMonitor": (0, "(uint64 600000,)")})
    here = _bus({"org.gnome.Mutter.IdleMonitor": (0, "(uint64 4200,)")})
    assert hw.user_away(run=away, x11=_no_x11) is True
    assert hw.user_away(run=here, x11=_no_x11) is False


def test_screensaver_when_there_is_no_mutter() -> None:
    run = _bus({"org.freedesktop.ScreenSaver": (0, "(uint32 125000,)")})
    assert hw.idle_seconds(run=run, x11=_no_x11) == 125.0
    assert run.calls == ["org.gnome.Mutter.IdleMonitor", "org.freedesktop.ScreenSaver"]


def test_x11_when_no_bus_answers() -> None:
    assert hw.idle_seconds(run=_bus({}), x11=lambda: 90_000) == 90.0


def test_unknown_when_nothing_answers() -> None:
    assert hw.idle_seconds(run=_bus({}), x11=_no_x11) is None
    assert hw.user_away(run=_bus({}), x11=_no_x11) is None


def test_a_garbled_reply_falls_through() -> None:
    run = _bus({"org.gnome.Mutter.IdleMonitor": (0, "()"), "org.freedesktop.ScreenSaver": (0, "(uint32 7000,)")})
    assert hw.idle_seconds(run=run, x11=_no_x11) == 7.0


def test_windows_has_no_idle_reader_here(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert hw.idle_seconds(run=_bus({"org.gnome.Mutter.IdleMonitor": (0, "(uint64 1,)")})) is None
