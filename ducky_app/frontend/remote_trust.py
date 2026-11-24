"""Who may reach this PC remotely, decided on this PC (never by the site).

- This PC has one P-256 identity key, sealed by :mod:`backend.agent.secrets` (DPAPI on
  Windows, the Secret Service seal on Linux). The site never sees the private half.
- A phone, browser or other PC is a viewer key the person approved in this PC's window
  after checking that :func:`sas` shows the same code on both screens.
- Signatures are raw ``r||s`` (64 bytes, base64): the format WebCrypto ECDSA makes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

PC_KEY = "remote_pc_key"
VIEWERS_KEY = "remote_viewers"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_LOCK = threading.RLock()


class TrustError(ValueError):
    """A key or signature that can't be used."""


# --------------------------------------------------------------------------- this PC


def pc_key() -> ec.EllipticCurvePrivateKey:
    from backend.agent import secrets

    with _LOCK:
        pem = secrets.get_key(PC_KEY)
        if pem:
            key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
            if isinstance(key, ec.EllipticCurvePrivateKey):
                return key
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode("ascii")
        secrets.set_key(PC_KEY, pem)
        return key


def _spki(pub: ec.EllipticCurvePublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def pc_pub_b64() -> str:
    return base64.b64encode(_spki(pc_key().public_key())).decode("ascii")


def key_protection() -> str:
    """How the PC key is kept: ``dpapi`` | ``keyring`` | ``file`` (Linux, no Secret Service)."""
    import sys

    if sys.platform == "win32":
        return "dpapi"
    from backend.agent import secrets

    try:
        return "file" if secrets.master_key_path().is_file() else "keyring"
    except Exception:
        return "file"


# --------------------------------------------------------------------------- keys


def load_pub(pub_b64: str) -> ec.EllipticCurvePublicKey:
    """A P-256 SPKI public key from base64, or ``TrustError``."""
    try:
        raw = base64.b64decode(str(pub_b64 or ""), validate=True)
        key = serialization.load_der_public_key(raw)
    except Exception as exc:
        raise TrustError("not a public key") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise TrustError("not a P-256 key")
    return key


def pub_id(pub_b64: str) -> str:
    return hashlib.sha256(_spki(load_pub(pub_b64))).hexdigest()[:16]


def sas(pc_pub_b64_: str, viewer_pub_b64: str) -> str:
    """6 Crockford base32 chars from sha256(pc SPKI || viewer SPKI): both screens show it."""
    digest = hashlib.sha256(_spki(load_pub(pc_pub_b64_)) + _spki(load_pub(viewer_pub_b64))).digest()
    bits = int.from_bytes(digest[:4], "big") >> 2  # first 30 bits
    return "".join(_CROCKFORD[(bits >> (25 - 5 * i)) & 31] for i in range(6))


# --------------------------------------------------------------------------- signatures


def sign(msg: bytes) -> str:
    r, s = decode_dss_signature(pc_key().sign(msg, ec.ECDSA(hashes.SHA256())))
    return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode("ascii")


def verify_with(pub_b64: str, msg: bytes, sig_b64: str) -> bool:
    try:
        raw = base64.b64decode(str(sig_b64 or ""), validate=True)
    except ValueError:
        return False
    if len(raw) != 64:
        return False  # DER or anything else: only WebCrypto's raw r||s
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    try:
        load_pub(pub_b64).verify(der, msg, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, TrustError):
        return False


def verify(viewer_id: str, msg: bytes, sig_b64: str) -> bool:
    """True only for a currently approved viewer's valid signature."""
    row = approved().get(str(viewer_id or ""))
    return bool(row) and verify_with(row["pub"], msg, sig_b64)


# --------------------------------------------------------------------------- approved viewers


def approved() -> dict[str, dict[str, Any]]:
    from backend.agent import secrets

    try:
        data = json.loads(secrets.get_key_fresh(VIEWERS_KEY) or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(rows: dict[str, dict[str, Any]]) -> None:
    from backend.agent import secrets

    if rows:
        secrets.set_key(VIEWERS_KEY, json.dumps(rows, separators=(",", ":")))
    else:
        secrets.clear_key(VIEWERS_KEY)


def approve(pub_b64: str, label: str) -> str:
    """Pin a viewer key (called only from this PC's own window). Returns its id."""
    vid = pub_id(pub_b64)
    with _LOCK:
        rows = approved()
        rows[vid] = {"pub": pub_b64, "label": str(label or "Device").strip()[:40], "added": int(time.time())}
        _save(rows)
    return vid


def revoke(viewer_id: str) -> bool:
    with _LOCK:
        rows = approved()
        gone = rows.pop(str(viewer_id or ""), None) is not None
        if gone:
            _save(rows)
    return gone
