"""A second boot of the same listener keeps the running one — no Unreal required.

Regression: Ducky writes the same boot file to the island, to Documents and to
Epic's EditorToolset, and UEFN runs more than one of them at launch. Each copy
called ``bootstrap.run()``, and the second found the first's server, stopped it
(a ~0.4 s game-thread hitch while ``shutdown()`` waits out ``serve_forever``) and
started an identical one in its place.
"""

from __future__ import annotations

import socket
import sys
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
    unreal.register_slate_post_tick_callback = lambda _fn: object()
    unreal.unregister_slate_post_tick_callback = lambda _h: None
    monkeypatch.setitem(sys.modules, "unreal", unreal)

    for name in [m for m in sys.modules if m == "listener" or m.startswith("listener.")]:
        monkeypatch.delitem(sys.modules, name)
    # The real handlers, tick and status window pull in the whole editor surface (and Tk).
    tick = types.ModuleType("listener.tick")
    tick.tick_handler = lambda _dt: None
    status_window = types.ModuleType("listener.status_window")
    status_window.MCPStatusWindow = object
    handlers = types.ModuleType("listener.handlers")
    monkeypatch.setitem(sys.modules, "listener.tick", tick)
    monkeypatch.setitem(sys.modules, "listener.status_window", status_window)
    monkeypatch.setitem(sys.modules, "listener.handlers", handlers)
    monkeypatch.setattr(sys, "path", [str(_LISTENER_ROOT), *sys.path])
    monkeypatch.setenv("UEFN_DUCKY_LISTENER_PORT", str(_free_port()))

    import listener.bootstrap as bootstrap
    import listener.runtime as runtime

    monkeypatch.setattr(bootstrap, "_epic_mcp_tcp_up", lambda *_a, **_k: False)
    stops: list[int] = []
    monkeypatch.setattr(bootstrap, "stop_listener", lambda: stops.append(1) or runtime.stop_listener())
    bootstrap._test_stops = stops
    yield bootstrap
    if unreal._mcp_server is not None:
        runtime.stop_listener()


def test_a_second_boot_keeps_the_running_listener(boot) -> None:
    unreal = sys.modules["unreal"]
    boot.run(ensure_epic=False)
    server = unreal._mcp_server
    assert server is not None

    boot.run(ensure_epic=False)

    assert boot._test_stops == []
    assert unreal._mcp_server is server


def test_a_listener_from_other_code_is_still_replaced(boot) -> None:
    unreal = sys.modules["unreal"]
    boot.run(ensure_epic=False)
    server = unreal._mcp_server
    unreal._mcp_loaded_stamp = "0:0"  # started by an older copy of the source

    boot.run(ensure_epic=False)

    assert boot._test_stops == [1]
    assert unreal._mcp_server is not None and unreal._mcp_server is not server


def test_a_dead_listener_is_replaced(boot) -> None:
    unreal = sys.modules["unreal"]
    boot.run(ensure_epic=False)
    server = unreal._mcp_server
    server.shutdown()
    unreal._mcp_server_thread.join(timeout=3.0)

    boot.run(ensure_epic=False)

    assert boot._test_stops == [1]
    assert unreal._mcp_server is not server
