"""UEFN closed: no caller, background or tool, opens a connection to the listener or Epic's MCP."""

from __future__ import annotations

import socket
import urllib.request

import pytest

import backend.bridge.client as bridge
from backend.bridge import editor_presence

UEFN_EXE = "unrealeditorfortnite-win64-shipping.exe"
LISTENER_PORT = 4200
EPIC_PORT = 8000
TEN_MIN = 600.0


@pytest.fixture
def processes(monkeypatch: pytest.MonkeyPatch) -> dict:
    """The fake process list (``names``) and how often it was read (``scans``); time is mocked."""
    state = {"names": ["explorer.exe", "ducky.exe"], "scans": 0, "now": 0.0}

    def _names():
        state["scans"] += 1
        return list(state["names"])

    monkeypatch.setattr(editor_presence, "_process_names", _names)
    monkeypatch.setattr(editor_presence, "_mono", lambda: state["now"])
    monkeypatch.delenv(editor_presence.ALWAYS_PROBE_ENV, raising=False)
    editor_presence.forget()
    yield state
    editor_presence.forget()


@pytest.fixture
def connects(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    """Every TCP connect and urlopen made in the test, all refused (nothing listens)."""
    seen: list[tuple[str, int]] = []

    def _connect(address, *a, **k):
        seen.append((str(address[0]), int(address[1])))
        raise ConnectionRefusedError(10061, "refused")

    def _urlopen(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        from urllib.parse import urlparse

        parsed = urlparse(url)
        seen.append((parsed.hostname or "", parsed.port or 80))
        raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))

    import urllib.error

    monkeypatch.setattr(socket, "create_connection", _connect)
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    bridge._invalidate_cache()
    monkeypatch.setattr(bridge, "_pinned_port", None)
    monkeypatch.setattr(bridge, "_discovered_port", None)
    return seen


# --------------------------------------------------------------------------- the presence check


def test_no_editor_process_reads_closed(processes) -> None:
    assert editor_presence.uefn_running() is False
    assert editor_presence.editor_running() is False


def test_uefn_and_unreal_editor_are_told_apart(processes) -> None:
    processes["names"].append("UnrealEditor.exe".lower())
    editor_presence.forget()
    assert editor_presence.uefn_running() is False
    assert editor_presence.editor_running() is True
    processes["names"].append(UEFN_EXE)
    editor_presence.forget()
    assert editor_presence.uefn_running() is True


def test_process_list_is_read_once_per_cache_window(processes) -> None:
    for _ in range(50):
        editor_presence.uefn_running()
    assert processes["scans"] == 1
    processes["names"].append(UEFN_EXE)
    assert editor_presence.uefn_running() is False  # still the cached answer
    processes["now"] += editor_presence.CACHE_SEC
    assert editor_presence.uefn_running() is True
    assert processes["scans"] == 2


def test_unreadable_process_list_never_hides_an_editor(monkeypatch) -> None:
    monkeypatch.setattr(editor_presence, "_process_names", lambda: None)
    editor_presence.forget()
    try:
        assert editor_presence.uefn_running() is True
    finally:
        editor_presence.forget()


def test_env_switch_turns_the_check_off(processes, monkeypatch) -> None:
    monkeypatch.setenv(editor_presence.ALWAYS_PROBE_ENV, "1")
    assert editor_presence.uefn_running() is True
    assert processes["scans"] == 0


# --------------------------------------------------------------------------- the client's gate


def test_uefn_closed_listener_calls_connect_nowhere(processes, connects) -> None:
    assert bridge.listener_get_health(LISTENER_PORT) is None
    assert bridge.listener_ping_port(LISTENER_PORT) is False
    with pytest.raises(ConnectionError, match="UEFN isn't open with an island"):
        bridge.send_command("spawn_actor", {"x": 1})
    with pytest.raises(ConnectionError, match="UEFN isn't open with an island"):
        bridge.post_command_to_listener(LISTENER_PORT, "get_log")
    with pytest.raises(ConnectionError):
        bridge.discover_port()
    assert connects == []


def test_uefn_open_the_same_calls_do_connect(processes, connects) -> None:
    processes["names"].append(UEFN_EXE)
    editor_presence.forget()
    assert bridge.listener_get_health(LISTENER_PORT) is None  # refused: listener not up yet
    assert (("127.0.0.1", LISTENER_PORT)) in connects


def test_epic_probe_skips_loopback_without_an_editor(processes, connects) -> None:
    from backend.mcp_plugins.epic import epic_url_reachable, tcp_probe_url

    assert epic_url_reachable(f"http://127.0.0.1:{EPIC_PORT}/mcp") is False
    assert connects == []
    assert epic_url_reachable("http://10.0.0.5:8000/mcp") is False  # a remote host is still tried
    assert connects == [("10.0.0.5", 8000)]
    # Other local MCP plugins (Blender, a plugin's own server) are probed as before.
    assert tcp_probe_url("http://127.0.0.1:9876/mcp") is False
    assert connects[-1] == ("127.0.0.1", 9876)


# --------------------------------------------------------------------------- 10 idle minutes


def _idle_round(status_state, island_watch) -> None:
    """One round of every background reader that used to reach the listener or Epic's MCP."""
    from backend.bridge import status
    from backend.mcp_plugins.epic import probe_epic_mcp

    # The panel's status cache refresh and an agent turn's prompt status.
    status.fetch_listener_status(LISTENER_PORT, state=status_state, version="t")
    status.fetch_listener_status(LISTENER_PORT)
    # Prompt / window boot / workflow save checks read health directly.
    bridge.listener_get_health(LISTENER_PORT)
    bridge.listener_get_health(LISTENER_PORT, timeout=0.35)
    status.is_listener_ready(LISTENER_PORT)
    # The plugin pool's Epic probe (client_pool) and ducky_get_status.
    probe_epic_mcp(ttl_sec=0)
    # The island watch's round, had a UEFN call started it.
    island_watch.tick()
    # A tool server's port discovery.
    try:
        bridge.discover_port()
    except ConnectionError:
        pass


def test_ten_idle_minutes_with_uefn_closed_make_zero_connections(processes, connects, monkeypatch) -> None:
    from backend.bridge import status
    from backend.mcp_plugins import epic
    from frontend.ui_web.verse_editor.urc import island_watch

    monkeypatch.setattr(epic, "epic_mcp_enabled", lambda: True)
    monkeypatch.setattr("frontend.deploy.quarantine_project_python", lambda root, deep=False: [])
    monkeypatch.setattr("frontend.deploy.refresh_inits", lambda root: None)
    monkeypatch.setattr(island_watch, "_write_doc", lambda doc: None)
    monkeypatch.setattr(island_watch, "_write_locks", lambda doc: None)
    island_watch.reset_for_tests()
    state = status.ListenerStatusState()
    rounds = 0
    while processes["now"] < TEN_MIN:  # the old panel poll ran every ~7 s
        _idle_round(state, island_watch)
        processes["now"] += 7.0
        rounds += 1
    island_watch.reset_for_tests()
    assert rounds >= 85
    assert connects == []
    assert processes["scans"] <= TEN_MIN / editor_presence.CACHE_SEC + 1


def test_the_idle_round_does_connect_when_uefn_runs(processes, connects, monkeypatch) -> None:
    """Proof the count above can see connections: the same round with UEFN running."""
    from backend.bridge import status
    from backend.mcp_plugins import epic
    from frontend.ui_web.verse_editor.urc import island_watch

    processes["names"].append(UEFN_EXE)
    editor_presence.forget()
    monkeypatch.setattr(epic, "epic_mcp_enabled", lambda: True)
    monkeypatch.setattr(epic, "_panel_project_root", lambda: "")
    monkeypatch.setattr("frontend.deploy.quarantine_project_python", lambda root, deep=False: [])
    monkeypatch.setattr("frontend.deploy.refresh_inits", lambda root: None)
    monkeypatch.setattr(island_watch, "_write_doc", lambda doc: None)
    monkeypatch.setattr(island_watch, "_write_locks", lambda doc: None)
    island_watch.reset_for_tests()
    _idle_round(status.ListenerStatusState(), island_watch)
    island_watch.reset_for_tests()
    ports = {port for _, port in connects}
    assert LISTENER_PORT in ports and EPIC_PORT in ports
