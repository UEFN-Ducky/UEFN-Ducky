"""The Fortnite client tools (UEFN plugin): no input while it loads, never resize it,
always release the key. On Oct 2 2026 a client that was focused and sent keys mid-load
hung on its GPU and closed."""

from __future__ import annotations

import time

import pytest

from backend.tools.uefn import fortnite_client as fc


def _log(tmp_path, lines: list[str]):
    path = tmp_path / "FortniteGame.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_play_mode_comes_from_the_client_log(tmp_path) -> None:
    assert fc.gameplay_loaded_for(_log(tmp_path, ["[2026.10.02-10.29.32:958][696]LogCore: loading"])) is None
    stamp = time.strftime("[%Y.%m.%d-%H.%M.%S:000][701]", time.gmtime(time.time() - 30))
    age = fc.gameplay_loaded_for(_log(tmp_path, [stamp + "LogValkyriePerformanceHUDBase: [3eb5] we are in playmode"]))
    assert 28 <= age <= 35  # the stamps are UTC


class FakeUser32:
    def __init__(self, *, iconic=False, foreground=111, lose_focus_after=None):
        self.iconic = iconic
        self.fg = foreground
        self.calls: list[tuple] = []
        self.lose_focus_after = lose_focus_after
        self._polls = 0

    def IsIconic(self, hwnd):
        return self.iconic

    def ShowWindow(self, hwnd, cmd):
        self.calls.append(("ShowWindow", cmd))

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("SetForegroundWindow",))
        self.fg = hwnd

    def GetForegroundWindow(self):
        if self.lose_focus_after is not None:
            self._polls += 1
            if self._polls > self.lose_focus_after:
                return 999
        return self.fg


@pytest.fixture()
def client(monkeypatch):
    u = FakeUser32(foreground=5)
    keys: list[tuple[str, bool]] = []
    monkeypatch.setattr(fc, "_client", lambda: (u, 111, "Fortnite"))
    monkeypatch.setattr(fc, "_shot", lambda u, hwnd, label: {"kind": "image", "path": f"{label}.png", "name": label})
    monkeypatch.setattr(fc, "_send_key", lambda u, key, up: keys.append((key, up)))
    monkeypatch.setattr(fc.time, "sleep", lambda s: None)
    return u, keys


@pytest.mark.pc_only("the Fortnite client runs on the PC")
def test_nothing_touches_a_loading_client(client, monkeypatch) -> None:
    u, keys = client
    monkeypatch.setattr(fc, "gameplay_loaded_for", lambda path=None: None)
    cap = fc.client_capture()
    move = fc.client_move("W", 1.0)
    assert cap["ok"] is False and "still loading" in cap["error"]
    assert move["ok"] is False and "still loading" in move["error"]
    assert u.calls == [] and keys == []


@pytest.mark.pc_only("the Fortnite client runs on the PC")
def test_a_loaded_client_is_focused_without_being_resized(client, monkeypatch) -> None:
    u, _ = client
    monkeypatch.setattr(fc, "gameplay_loaded_for", lambda path=None: 60.0)
    out = fc.client_capture()
    assert out["ok"] is True and out["path"] == "Fortnite client.png"
    assert ("ShowWindow", 9) not in u.calls  # SW_RESTORE only for a minimized window
    u.iconic, u.fg = True, 5
    fc.client_capture()
    assert ("ShowWindow", 9) in u.calls


@pytest.mark.pc_only("the Fortnite client runs on the PC")
def test_move_holds_one_key_and_always_releases_it(client, monkeypatch) -> None:
    _u, keys = client
    monkeypatch.setattr(fc, "gameplay_loaded_for", lambda path=None: 60.0)
    clock = iter(x * 0.1 for x in range(1000))
    monkeypatch.setattr(fc.time, "monotonic", lambda: next(clock))
    out = fc.client_move("w", 0.5)
    assert out["ok"] is True and out["before"] == "Before.png" and out["after"] == "After.png"
    assert out["movement_verified"] is False
    assert keys == [("W", False), ("W", True)]


@pytest.mark.pc_only("the Fortnite client runs on the PC")
def test_losing_focus_mid_move_releases_the_key_and_fails(monkeypatch) -> None:
    u = FakeUser32(foreground=111, lose_focus_after=2)
    keys: list[tuple[str, bool]] = []
    monkeypatch.setattr(fc, "_client", lambda: (u, 111, "Fortnite"))
    monkeypatch.setattr(fc, "_shot", lambda u, hwnd, label: {"kind": "image", "path": f"{label}.png", "name": label})
    monkeypatch.setattr(fc, "_send_key", lambda u, key, up: keys.append((key, up)))
    monkeypatch.setattr(fc, "gameplay_loaded_for", lambda path=None: 60.0)
    monkeypatch.setattr(fc.time, "sleep", lambda s: None)
    out = fc.client_move("D", 3.0)
    assert out["ok"] is False and "lost focus" in out["error"]
    assert keys == [("D", False), ("D", True)]


def test_bad_input_is_refused_before_anything_happens() -> None:
    assert "W, A, S or D" in fc.client_move("Q")["error"]
    assert "between 0.1 and 3" in fc.client_move("W", 5)["error"]


def test_the_uefn_plugin_registers_three_client_nodes(monkeypatch) -> None:
    registered: dict[str, object] = {}

    class Api:
        def register_pipeline_node(self, node_type, handler):
            registered[node_type] = handler

    fc.register_nodes(Api())
    assert sorted(registered) == ["uefn.client.capture", "uefn.client.move", "uefn.client.wait"]
    monkeypatch.setattr(fc, "client_move", lambda key, seconds: {"ok": True, "key": key, "seconds": seconds})
    assert registered["uefn.client.move"]({"config": {"key": "A", "seconds": "0.5"}}) == {"ok": True, "key": "A", "seconds": 0.5}


@pytest.mark.pc_only("the Fortnite client runs on the PC")
def test_status_with_no_client_open_is_an_answer_not_an_error(monkeypatch) -> None:
    def none_open():
        raise fc.NoClientWindow("Expected exactly one visible Fortnite client window; found 0")

    monkeypatch.setattr(fc, "_client", none_open)
    status = fc.client_status()
    assert status["ok"] is True and status["running"] is False
    assert "play session" in status["hint"]

    def two_open():
        raise RuntimeError("Expected exactly one visible Fortnite client window; found 2")

    monkeypatch.setattr(fc, "_client", two_open)
    assert fc.client_status() == {"ok": False, "error": "Expected exactly one visible Fortnite client window; found 2"}
