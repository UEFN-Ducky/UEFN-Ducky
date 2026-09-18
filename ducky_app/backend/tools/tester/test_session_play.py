"""Epic-first start_game and wait/log helpers."""

from backend.tools.tester import session_play
from backend.tools.tester.session_play import wait_for_player, expect_log


def test_start_game_prefers_epic(monkeypatch):
    monkeypatch.setattr("backend.testing.live_graph.tester_uefn_status", lambda: {"epic_mcp_online": True})
    monkeypatch.setattr("backend.testing.live_graph._call_epic_tool", lambda *a, **k: '{"ok":true}')
    out = session_play.start_game()
    assert out["ok"] is True
    assert out["source"] == "epic"


def test_player_wait_times_out(monkeypatch):
    monkeypatch.setattr(
        session_play,
        "session_probe",
        lambda: {"ok": True, "playing": True, "has_player": False, "player_count": 0},
    )
    monkeypatch.setattr(session_play.time, "sleep", lambda *_: None)
    clock = {"t": 0.0}

    def fake_time():
        clock["t"] += 50.0
        return clock["t"]

    monkeypatch.setattr(session_play.time, "time", fake_time)
    out = wait_for_player(timeout_sec=1)
    assert out["ok"] is False
    assert out["has_player"] is False


def test_log_expect_hit_and_miss(monkeypatch):
    monkeypatch.setattr(session_play.time, "sleep", lambda *_: None)

    def fake_send(name, args):
        if args.get("regex") == "HIT":
            return {"lines": ["HIT ok"], "offset": 9}
        return {"lines": [], "offset": 1}

    monkeypatch.setattr("backend.bridge.send_command", fake_send)
    hit = expect_log("HIT", timeout_sec=1)
    assert hit["ok"] is True
    assert hit["count"] == 1
    clock = {"t": 0.0}

    def fake_time():
        clock["t"] += 50.0
        return clock["t"]

    monkeypatch.setattr(session_play.time, "time", fake_time)
    miss = expect_log("MISS", timeout_sec=1)
    assert miss["ok"] is False


def test_start_game_degrades_to_listener(monkeypatch):
    monkeypatch.setattr("backend.testing.live_graph.tester_uefn_status", lambda: {"epic_mcp_online": False})

    def boom(*_a, **_k):
        raise RuntimeError("no epic")

    monkeypatch.setattr("backend.testing.live_graph._call_epic_tool", boom)
    monkeypatch.setattr("backend.bridge.send_command", lambda *_a, **_k: {"started": True, "method": "editor_play_simulate"})
    out = session_play.start_game()
    assert out["ok"] is True
    assert out["degraded"] is True
    assert out["source"] == "listener"
