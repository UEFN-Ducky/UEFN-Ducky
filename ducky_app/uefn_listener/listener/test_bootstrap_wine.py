"""Under Wine, Epic MCP binds :8000 late: Ducky waits for it instead of calling StartServer."""

from __future__ import annotations

import socket
import sys
import threading
import time
import types
from pathlib import Path

import pytest

_LISTENER_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def boot(monkeypatch):
    unreal = types.ModuleType("unreal")
    unreal.log = unreal.log_warning = unreal.log_error = lambda *_a, **_k: None
    ticks: list = []
    unreal.register_slate_post_tick_callback = lambda fn: ticks.append(fn) or fn
    unreal.unregister_slate_post_tick_callback = lambda h: ticks.remove(h)
    monkeypatch.setitem(sys.modules, "unreal", unreal)
    for name in [m for m in sys.modules if m == "listener" or m.startswith("listener.")]:
        monkeypatch.delitem(sys.modules, name)
    for name, attrs in (
        ("listener.tick", {"tick_handler": lambda _dt: None}),
        ("listener.status_window", {"MCPStatusWindow": object}),
        ("listener.handlers", {}),
    ):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(sys, "path", [str(_LISTENER_ROOT), *sys.path])

    import listener.bootstrap as bootstrap

    monkeypatch.setattr(bootstrap, "_EPIC_PORT", _free_port())
    started: list[int] = []
    monkeypatch.setattr(bootstrap, "_ensure_epic_mcp", lambda: started.append(1))
    bootstrap._test_ticks, bootstrap._test_started = ticks, started
    return bootstrap


def _pump(boot, seconds: float) -> None:
    end = time.monotonic() + seconds
    while boot._test_ticks and time.monotonic() < end:
        for fn in list(boot._test_ticks):
            fn(0.016)
        time.sleep(0.02)


def test_a_late_epic_server_is_waited_for_not_started(boot) -> None:
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def late_bind() -> None:
        time.sleep(0.6)  # Wine's slow start, scaled down
        srv.bind(("127.0.0.1", boot._EPIC_PORT))
        srv.listen()

    threading.Thread(target=late_bind, daemon=True).start()
    try:
        boot._defer_epic_check(grace=10.0, every=0.1)
        _pump(boot, 5.0)
    finally:
        time.sleep(0.05)
        srv.close()
    assert boot._test_ticks == [] and boot._test_started == []


def test_no_epic_server_after_the_grace_starts_it(boot) -> None:
    boot._defer_epic_check(grace=0.3, every=0.05)
    _pump(boot, 3.0)
    assert boot._test_ticks == [] and boot._test_started == [1]


def test_wine_is_seen_from_the_prefix_env(boot, monkeypatch) -> None:
    monkeypatch.setenv("WINEPREFIX", "/home/me/pfx")
    assert boot._under_wine() is True
