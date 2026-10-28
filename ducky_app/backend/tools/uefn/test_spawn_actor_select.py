"""spawn_actor (MCP tool) does not select the new actor unless asked."""

from __future__ import annotations


def _spawn(monkeypatch):
    from backend.tools.uefn import actors as actors_mod

    sent: list = []
    monkeypatch.setattr(actors_mod, "send_command", lambda cmd, params: sent.append((cmd, params)) or {"ok": True})
    return getattr(actors_mod.spawn_actor, "fn", actors_mod.spawn_actor), sent


def test_the_persons_selection_is_left_alone_by_default(monkeypatch):
    spawn, sent = _spawn(monkeypatch)
    spawn(asset_path="/Game/Props/Rock.Rock_C")
    assert sent == [("spawn_actor", {"select": False, "asset_path": "/Game/Props/Rock.Rock_C"})]


def test_select_is_still_passed_when_asked(monkeypatch):
    spawn, sent = _spawn(monkeypatch)
    spawn(asset_path="/Game/Props/Rock.Rock_C", select=True)
    assert sent[0][1]["select"] is True
