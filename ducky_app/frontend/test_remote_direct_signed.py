"""rtc_connect through the mailbox: only an approved device's fresh signature opens the PC,
and the answer comes back signed by this PC (the site relaying it can't swap either SDP)."""

from __future__ import annotations

import base64
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from frontend import duckyos_account as acc
from frontend import remote_direct
from frontend import remote_trust as rt

OFFER = {"type": "offer", "sdp": "v=0\r\na=fingerprint:sha-256 AA:BB\r\n"}
ANSWER = {"type": "answer", "sdp": "v=0\r\na=fingerprint:sha-256 CC:DD\r\n"}


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    from backend.agent import secrets

    store: dict[str, str] = {}
    monkeypatch.setattr(secrets, "get_key", store.get)
    monkeypatch.setattr(secrets, "get_key_fresh", store.get)
    monkeypatch.setattr(secrets, "set_key", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(secrets, "clear_key", lambda k: store.pop(k, None))
    monkeypatch.setattr(remote_direct, "rtc_connect", lambda raw: {"answer": ANSWER, "protocol": 1})
    rt._SEEN_SIGS.clear()
    return store


def _device(approve: bool = True):
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    pub = base64.b64encode(spki).decode("ascii")
    if approve:
        rt.approve(pub, "Phone")
    return key, rt.pub_id(pub)


def _signed(key, viewer: str, ts: int | None = None, session: str = "s1") -> dict:
    ts = int(time.time()) if ts is None else ts
    r, s = decode_dss_signature(key.sign(rt.offer_message(session, ts, OFFER["sdp"]), ec.ECDSA(hashes.SHA256())))
    sig = base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode("ascii")
    return {"session": session, "offer": OFFER, "protocol": 1, "viewer": viewer, "ts": ts, "sig": sig}


def test_an_unsigned_offer_is_told_to_pair() -> None:
    out = acc.dispatch_desktop_rpc("rtc_connect", {"session": "s1", "offer": OFFER, "protocol": 1})
    assert out["ok"] is False and out["result"]["pair"] is True


def test_unapproved_stale_and_replayed_offers_are_refused() -> None:
    stranger, sid = _device(approve=False)
    assert "isn't approved" in acc.dispatch_desktop_rpc("rtc_connect", _signed(stranger, sid))["error"]
    key, vid = _device()
    assert "too old" in acc.dispatch_desktop_rpc("rtc_connect", _signed(key, vid, ts=int(time.time()) - 600))["error"]
    good = _signed(key, vid)
    assert acc.dispatch_desktop_rpc("rtc_connect", good)["ok"] is True
    assert "already used" in acc.dispatch_desktop_rpc("rtc_connect", good)["error"]


def test_a_tampered_offer_fails_and_a_good_answer_verifies() -> None:
    key, vid = _device()
    bad = _signed(key, vid)
    bad["offer"] = {"type": "offer", "sdp": OFFER["sdp"] + "a=fingerprint:sha-256 EE:FF\r\n"}
    assert acc.dispatch_desktop_rpc("rtc_connect", bad)["ok"] is False
    args = _signed(key, vid)
    out = acc.dispatch_desktop_rpc("rtc_connect", args)
    assert out["ok"] and rt.check_answer(rt.pc_pub_b64(), args, out["result"])
    swapped = {**out["result"], "answer": {"type": "answer", "sdp": "v=0\r\na=fingerprint:sha-256 00:11\r\n"}}
    assert not rt.check_answer(rt.pc_pub_b64(), args, swapped)


def test_opening_another_pc_pairs_first_then_checks_its_answer(monkeypatch) -> None:
    """PanelApiStoreMixin.ducky_link_rtc_connect: unpaired -> pair_request + pin + 'approve'
    message; paired -> the answer must carry the pinned PC's signature."""
    from frontend import ducky_link
    from frontend.ui_web.panel_api_store import PanelApiStoreMixin as api

    other = {"approved": False}

    def fake_call(to, method, args=None, timeout=30.0):
        if not other["approved"]:  # what ducky_link.call says on first contact
            return {"ok": False, "pair": True, "error": "Approve this PC on the other UEFN Ducky (code ABC123), then try again."}
        return acc.dispatch_desktop_rpc(method, args)

    monkeypatch.setattr(ducky_link, "call", fake_call)
    with pytest.raises(RuntimeError, match="Approve this PC"):
        api.ducky_link_rtc_connect(None, {"to": "k2", "session": "s3", "offer": OFFER, "protocol": 1})
    rt.pin_pc("k2", rt.pc_pub_b64())
    rt.approve(rt.pc_pub_b64(), "UEFN Ducky PC")
    other["approved"] = True
    got = api.ducky_link_rtc_connect(None, {"to": "k2", "session": "s4", "offer": OFFER, "protocol": 1})
    assert got["answer"] == ANSWER
    rt.pin_pc("k2", base64.b64encode(ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode("ascii"))
    with pytest.raises(RuntimeError, match="could not be verified"):
        api.ducky_link_rtc_connect(None, {"to": "k2", "session": "s5", "offer": OFFER, "protocol": 1})


def test_another_pc_signs_its_own_offer_once_approved() -> None:
    rt.approve(rt.pc_pub_b64(), "UEFN Ducky PC")  # the same key plays both PCs here
    args = rt.sign_offer({"session": "s2", "offer": OFFER, "protocol": 1})
    assert acc.dispatch_desktop_rpc("rtc_connect", args)["ok"] is True
