"""remote_trust: the PC key, approved viewers, WebCrypto-format signatures, the pairing code."""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from frontend import remote_trust as rt


@pytest.fixture(autouse=True)
def vault(monkeypatch):
    from backend.agent import secrets

    store: dict[str, str] = {}
    monkeypatch.setattr(secrets, "get_key", store.get)
    monkeypatch.setattr(secrets, "get_key_fresh", store.get)
    monkeypatch.setattr(secrets, "set_key", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(secrets, "clear_key", lambda k: store.pop(k, None))
    monkeypatch.setattr(rt, "_announce", lambda vid, row: None)
    rt._PENDING.clear()
    yield store
    rt._PENDING.clear()


def _viewer() -> tuple[ec.EllipticCurvePrivateKey, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, base64.b64encode(spki).decode("ascii")


def _raw_sig(key: ec.EllipticCurvePrivateKey, msg: bytes) -> str:
    r, s = decode_dss_signature(key.sign(msg, ec.ECDSA(hashes.SHA256())))
    return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode("ascii")


def test_pc_key_is_made_once_and_signs_in_webcrypto_format(vault) -> None:
    first = rt.pc_pub_b64()
    assert rt.pc_pub_b64() == first and "BEGIN PRIVATE KEY" in vault[rt.PC_KEY]
    sig = rt.sign(b"hello")
    assert len(base64.b64decode(sig)) == 64
    assert rt.verify_with(first, b"hello", sig)
    assert not rt.verify_with(first, b"hellO", sig)


def test_only_an_approved_unrevoked_viewer_verifies() -> None:
    key, pub = _viewer()
    sig = _raw_sig(key, b"rtc1|s|1")
    vid = rt.pub_id(pub)
    assert not rt.verify(vid, b"rtc1|s|1", sig)  # not approved yet
    assert rt.approve(pub, "Pixel 9") == vid
    assert rt.verify(vid, b"rtc1|s|1", sig)
    assert rt.approved()[vid]["label"] == "Pixel 9"
    assert rt.revoke(vid) and not rt.verify(vid, b"rtc1|s|1", sig)


def test_der_signatures_and_other_curves_are_refused() -> None:
    key, pub = _viewer()
    der = base64.b64encode(key.sign(b"m", ec.ECDSA(hashes.SHA256()))).decode("ascii")
    assert not rt.verify_with(pub, b"m", der)
    other = ec.generate_private_key(ec.SECP384R1()).public_key()
    p384 = base64.b64encode(other.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo))
    with pytest.raises(rt.TrustError):
        rt.load_pub(p384.decode("ascii"))
    with pytest.raises(rt.TrustError):
        rt.load_pub("not base64!")


def test_a_request_waits_for_this_pcs_window_and_only_three_wait() -> None:
    _, pub = _viewer()
    out = rt.pair_request({"pub": pub, "label": "  My\nPhone  "})
    assert out["ok"] and out["result"]["pending"] and out["result"]["code"] == rt.sas(rt.pc_pub_b64(), pub)
    assert rt.pending_requests()[0]["label"] == "My Phone"
    assert rt.pair_status({"pub": pub})["result"] == {"approved": False, "pending": True}
    assert rt.decide(rt.pub_id(pub), True)
    assert rt.pair_status({"pub": pub})["result"] == {"approved": True, "pending": False}
    assert rt.pair_request({"pub": pub})["result"]["approved"] is True
    for _ in range(rt.PENDING_MAX):
        assert rt.pair_request({"pub": _viewer()[1]})["ok"]
    assert rt.pair_request({"pub": _viewer()[1]})["ok"] is False
    assert rt.pair_request({"pub": "junk"})["ok"] is False


def test_denied_request_is_not_approved_and_mailbox_routes_pairing() -> None:
    from frontend import duckyos_account as acc

    _, pub = _viewer()
    got = acc.dispatch_desktop_rpc("pair_request", {"pub": pub, "label": "Tab"})
    assert got["ok"] and got["result"]["pending"]
    assert rt.decide(rt.pub_id(pub), False)
    assert acc.dispatch_desktop_rpc("pair_status", {"pub": pub})["result"]["approved"] is False
    assert {"pair_request", "pair_status"} <= acc.WEBSITE_RPC


def test_sas_is_stable_six_crockford_chars_and_order_matters() -> None:
    _, a = _viewer()
    _, b = _viewer()
    code = rt.sas(a, b)
    assert code == rt.sas(a, b) and len(code) == 6
    assert set(code) <= set(rt._CROCKFORD)
    assert rt.sas(b, a) != code or a == b
