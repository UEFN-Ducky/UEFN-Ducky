"""UEFN Ducky to UEFN Ducky: sealed requests through a stand-in for the site mailbox."""

from __future__ import annotations

from typing import Any

import pytest

from frontend import ducky_link

ADK = bytes(range(32))


def _aad(sender: str, receiver: str, method: str, rid: str, direction: str) -> bytes:
    return ducky_link._aad("a_acct", sender, receiver, method, rid, direction)


def test_a_sealed_message_opens_only_for_its_own_request() -> None:
    aad = _aad("k1", "k2", "load_messages", "r1", "req")
    env = ducky_link.seal({"conversation_id": "c1", "text": "héllo"}, adk=ADK, aad=aad)
    assert set(env) == {"sealed"}
    # The ciphertext bytes, not their base64 text: a random nonce's base64 can spell "c1".
    import base64

    raw = base64.b64decode(env["sealed"])
    assert b'"conversation_id"' not in raw and b'"c1"' not in raw and "héllo".encode() not in raw
    assert ducky_link.open_sealed(env, adk=ADK, aad=aad) == {"conversation_id": "c1", "text": "héllo"}
    # Another request, another Ducky, the answer direction or another account's key: refused.
    for other in (
        _aad("k1", "k2", "load_messages", "r2", "req"),
        _aad("k1", "k3", "load_messages", "r1", "req"),
        _aad("k1", "k2", "send_message", "r1", "req"),
        _aad("k1", "k2", "load_messages", "r1", "res"),
    ):
        with pytest.raises(ducky_link.LinkError):
            ducky_link.open_sealed(env, adk=ADK, aad=other)
    with pytest.raises(ducky_link.LinkError):
        ducky_link.open_sealed(env, adk=bytes(32), aad=aad)
    with pytest.raises(ducky_link.LinkError):
        ducky_link.open_sealed({"sealed": "not base64!"}, adk=ADK, aad=aad)
    with pytest.raises(ducky_link.LinkError):
        ducky_link.open_sealed({"text": "plain"}, adk=ADK, aad=aad)


class _Site:
    """The uefn-ducky mailbox, one slot per Ducky, as the site plugin keeps it."""

    def __init__(self) -> None:
        self.slots: dict[str, dict[str, Any]] = {}
        self.me = "k1"

    def collect(self, plugin: str, event: str, body: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
        body = body or {}
        if event == "desktop-rpc":
            assert set(body["args"]) == {"sealed"}, "a Ducky's request leaves sealed"
            self.slots[body["keyId"]] = {"id": body["id"], "method": body["method"], "args": body["args"], "from": self.me, "status": "pending"}
            # The asked Ducky (k2) picks it up and answers right away.
            slot = self.slots[body["keyId"]]
            out = ducky_link.answer(slot["id"], slot["from"], slot["method"], slot["args"])
            slot.update(status="done", ok=out.get("ok", True), result=out.get("result"), error=out.get("error"))
            slot.pop("args")
            return {"id": body["id"], "ok": True}
        if event == "desktop-rpc-result":
            slot = self.slots.get(body["keyId"])
            if not slot or slot["id"] != body["id"]:
                return {"status": "gone"}
            return {"status": slot["status"], "ok": slot.get("ok"), "result": slot.get("result"), "error": slot.get("error")}
        raise AssertionError(event)


@pytest.fixture
def two_duckies(monkeypatch: pytest.MonkeyPatch) -> _Site:
    from frontend import duckyos_account

    site = _Site()
    monkeypatch.setattr(duckyos_account, "_plugin_collect", site.collect)
    # One account, one key; each side is "this Ducky" in turn.
    monkeypatch.setattr(ducky_link, "_context", lambda: ("a_acct", site.me, ADK))
    calls: list[tuple[str, dict[str, Any]]] = []

    def dispatch(method: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((method, dict(args or {})))
        return {"ok": True, "result": [{"id": "c1", "title": "Build the lobby"}]}

    monkeypatch.setattr(duckyos_account, "dispatch_desktop_rpc", dispatch)

    real_answer = ducky_link.answer

    def answer_as_k2(rid: str, sender: str, method: str, args: Any) -> dict[str, Any]:
        site.me = "k2"
        try:
            return real_answer(rid, sender, method, args)
        finally:
            site.me = "k1"

    monkeypatch.setattr(ducky_link, "answer", answer_as_k2)
    site.calls = calls  # type: ignore[attr-defined]
    return site


def test_one_ducky_runs_a_call_on_another_and_reads_the_sealed_answer(two_duckies: _Site) -> None:
    out = ducky_link.call("k2", "list_all_conversations", {"all_projects": True}, timeout=2)
    assert out == {"ok": True, "result": [{"id": "c1", "title": "Build the lobby"}]}
    assert two_duckies.calls == [("list_all_conversations", {"all_projects": True})]  # type: ignore[attr-defined]


def test_calls_that_never_leave(two_duckies: _Site) -> None:
    assert ducky_link.call("k1", "load_messages")["error"] == "That's this UEFN Ducky."
    assert "can't go" in ducky_link.call("k2", "set_mcp_config")["error"]
    assert ducky_link.call("k.2", "load_messages")["error"] == "Pick one of your UEFN Duckies."
    assert two_duckies.slots == {}


def test_an_answer_for_another_request_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from frontend import duckyos_account

    monkeypatch.setattr(ducky_link, "_context", lambda: ("a_acct", "k2", ADK))
    monkeypatch.setattr(duckyos_account, "dispatch_desktop_rpc", lambda m, a=None: {"ok": True, "result": "ran"})
    # Sealed for r1, delivered as r2 (a swapped mailbox row): the asked Ducky runs nothing.
    env = ducky_link.seal({}, adk=ADK, aad=_aad("k1", "k2", "list_folders", "r1", "req"))
    out = ducky_link.answer("r2", "k1", "list_folders", env)
    assert out["ok"] is False and "wasn't sealed for this request" in out["error"]
    ok = ducky_link.answer("r1", "k1", "list_folders", env)
    assert ok["ok"] is True and set(ok["result"]) == {"sealed"}


def test_the_link_is_only_for_this_pcs_own_windows() -> None:
    from frontend.duckyos_account import remote_denied

    for method in ("ducky_link_list", "ducky_link_call", "ducky_link_rtc_connect", "ducky_link_open"):
        assert remote_denied(method, on_this_pc=False), method
        assert not remote_denied(method, on_this_pc=True), method


def test_the_other_duckys_window_is_this_bundle_under_its_own_path() -> None:
    from frontend.ui_web.panel_httpd import bundle_rel
    from frontend.ui_web.remote_ducky_window import remote_ducky_url

    url = remote_ducky_url("http://127.0.0.1:4199/", "key_2", "Laptop Ducky")
    assert url == "http://127.0.0.1:4199/remote-ducky/?direct=ducky&to=key_2&name=Laptop%20Ducky"
    assert bundle_rel("remote-ducky/") == "index.html"
    assert bundle_rel("remote-ducky") == "index.html"
    assert bundle_rel("remote-ducky/assets/index-abc.js") == "assets/index-abc.js"
    assert bundle_rel("remote-ducky/sw.js") == "sw.js"
    # This Ducky's own paths stay as they are.
    assert bundle_rel("assets/index-abc.js") == "assets/index-abc.js"
    assert bundle_rel("remote-duckyx/a.js") == "remote-duckyx/a.js"
