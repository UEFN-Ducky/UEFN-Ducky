"""Off Windows with no local UEFN, editor calls run on a linked PC through a fake peer."""

from __future__ import annotations

import asyncio
import sys

import pytest

from backend.bridge import editor_gateway, editor_presence
from frontend import ducky_link

PC = {"keyId": "pc1", "name": "Desktop-PC", "live": True, "mine": False}


@pytest.fixture
def peer(monkeypatch):
    """A Linux Ducky with no UEFN and one live Windows PC; ``seen`` records each sealed call."""
    seen: list[dict] = []
    state = {"answer": True}

    def fake_call(to, method, args=None, *, timeout=30.0):
        seen.append({"to": to, "method": method, "args": args, "timeout": timeout})
        if not state["answer"]:
            return {"ok": False, "error": ducky_link.NOT_ANSWERING}
        if args["op"] == "ping":
            return {"ok": True, "result": {"pong": True}}
        return {"ok": True, "result": f"{args['op']} done"}

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(editor_presence, "uefn_running", lambda: False)
    monkeypatch.setattr(ducky_link, "list_duckies", lambda: {"ok": True, "devices": [{"keyId": "me", "mine": True, "live": True}, PC]})
    monkeypatch.setattr(ducky_link, "call", fake_call)
    editor_presence.forget()
    yield seen, state
    editor_presence.forget()


def test_ping_and_a_tool_call_run_on_the_linked_pc(peer) -> None:
    seen, _ = peer
    assert editor_presence.route() == "remote"
    out = editor_gateway.call("c", "ping", False, lambda: pytest.fail("no local UEFN"), arguments={})
    assert out == {"pong": True, "ran_on": "Desktop-PC"}

    async def epic():
        return await editor_gateway.acall(
            "c", "unreal__call_tool", True, lambda: pytest.fail("no local UEFN"), arguments={"toolset": "X"}
        )

    assert asyncio.run(epic()) == "unreal__call_tool done\n(ran on Desktop-PC)"
    from backend.bridge.call_limits import EPIC_CALL_LIMIT_S

    assert [s["method"] for s in seen] == [editor_gateway.REMOTE_METHOD] * 2
    assert seen[1] == {
        "to": "pc1",
        "method": "run_uefn_tool",
        "args": {"op": "unreal__call_tool", "arguments": {"toolset": "X"}},
        "timeout": EPIC_CALL_LIMIT_S,
    }


def test_a_pc_that_does_not_answer_is_a_timeout(peer) -> None:
    _, state = peer
    state["answer"] = False
    with pytest.raises(editor_gateway.EditorTimeout, match=r"Desktop-PC didn't answer ping"):
        editor_gateway.call("c", "ping", False, lambda: None, arguments={})


def test_heavy_calls_get_the_heavy_limit(peer) -> None:
    from backend.bridge.call_limits import EPIC_HEAVY_LIMIT_S

    seen, _ = peer
    editor_gateway.call("c", "import_asset", True, lambda: None, arguments={}, heavy=True)
    assert seen[-1]["timeout"] == EPIC_HEAVY_LIMIT_S


def test_local_uefn_or_windows_never_goes_remote(peer, monkeypatch) -> None:
    seen, _ = peer
    assert editor_gateway.call("c", "ping", False, lambda: "local", arguments=None) == "local"  # no arguments
    monkeypatch.setattr(editor_presence, "uefn_running", lambda: True)
    assert editor_presence.route() == "local"
    assert editor_gateway.call("c", "ping", False, lambda: "local", arguments={}) == "local"
    monkeypatch.setattr(editor_presence, "uefn_running", lambda: False)
    monkeypatch.setattr(sys, "platform", "win32")
    editor_presence.forget()
    assert editor_presence.linked_pc() is None
    assert seen == []


def test_no_linked_pc_is_route_none(peer, monkeypatch) -> None:
    monkeypatch.setattr(ducky_link, "list_duckies", lambda: {"ok": True, "devices": [{**PC, "live": False}]})
    editor_presence.forget()
    assert editor_presence.route() == "none"
