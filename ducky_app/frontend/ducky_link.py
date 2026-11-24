"""UEFN Ducky to UEFN Ducky: one signed-in UEFN Ducky asks another of the same
account to run an allowlisted panel call, through that Ducky's site mailbox.

- Sealed end to end with a key only the two PCs have: ECDH between their PC keys
  (:mod:`frontend.remote_trust`), each approved in the other's window. The site hands
  out the account data key (ADK), so the ADK seals only the public pairing messages
  (``PAIRING``), which the pairing code checked on both screens protects.
- The site relays only ciphertext, drops the request once answered and the answer
  once read (uefn-ducky ``desktop-rpc``).
- Every message is bound to its request (account, both Duckies, method, id and
  direction), so the site can't swap, redirect or replay one.
- The asked Ducky answers only while its Remote access is on (the mailbox waiter).

``rtc_connect`` over this link is how a window of this app opens another Ducky's whole
UI peer to peer (web ``remote/directTransport.ts``, bridge ``ducky``).
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
import zlib
from typing import Any

MAGIC = b"UDL1"  # ADK-sealed: pairing messages only
MAGIC_PC = b"UDL2"  # sealed with the two PCs' ECDH key
_INFO = b"uefn-ducky/ducky-link/v1"
_INFO_PC = b"uefn-ducky/ducky-link/v2"
PAIRING = frozenset({"pair_request", "pair_status"})
# The site takes at most this much sealed text per request or answer.
SEALED_MAX = 48_000
_KEY_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
NOT_ANSWERING = "That UEFN Ducky isn't answering. Open it, signed in, with Remote access on."


class LinkError(Exception):
    """A request couldn't go out, or its answer couldn't be read."""


def _derive(adk: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(adk)


def _aad(account: str, sender: str, receiver: str, method: str, rid: str, direction: str) -> bytes:
    return "|".join((account, sender, receiver, method, rid, direction)).encode("utf-8")


def pc_link_key(peer_pub_b64: str) -> bytes:
    """The AES key this PC and that PC both derive; the site can't (no private key)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    from frontend import remote_trust

    shared = remote_trust.pc_key().exchange(ec.ECDH(), remote_trust.load_pub(peer_pub_b64))
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO_PC).derive(shared)


def seal(obj: Any, *, adk: bytes = b"", aad: bytes, key: bytes = b"") -> dict[str, str]:
    """``{"sealed": base64(MAGIC + nonce + AES-256-GCM(zlib(json)))}``; ``key`` = a
    :func:`pc_link_key` (``UDL2``), else the ADK (``UDL1``, pairing only)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    plain = zlib.compress(json.dumps(obj, default=str, separators=(",", ":")).encode("utf-8"), 6)
    nonce = os.urandom(12)
    magic, aes = (MAGIC_PC, key) if key else (MAGIC, _derive(adk))
    box = magic + nonce + AESGCM(aes).encrypt(nonce, plain, aad)
    return {"sealed": base64.b64encode(box).decode("ascii")}


def open_sealed(env: Any, *, adk: bytes = b"", aad: bytes, key: bytes = b"") -> Any:
    """The object inside ``seal``'s envelope; ``LinkError`` for anything else (an ADK
    envelope where a PC key is expected included: no downgrade)."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    magic, aes = (MAGIC_PC, key) if key else (MAGIC, None)
    text = env.get("sealed") if isinstance(env, dict) else None
    try:
        box = base64.b64decode(str(text or ""), validate=True)
    except ValueError as exc:
        raise LinkError("not a sealed message") from exc
    if len(box) < len(magic) + 12 + 16 or not box.startswith(magic):
        raise LinkError("not a sealed message")
    nonce, body = box[len(magic) : len(magic) + 12], box[len(magic) + 12 :]
    try:
        plain = AESGCM(aes or _derive(adk)).decrypt(nonce, body, aad)
    except InvalidTag as exc:
        raise LinkError("This message wasn't sealed for this request by your account.") from exc
    return json.loads(zlib.decompress(plain).decode("utf-8"))


def _context() -> tuple[str, str, bytes]:
    """(account id, this Ducky's key id, the account data key) or ``LinkError``."""
    from backend.uefn_plugins import data_crypto
    from backend.uefn_plugins.scopes import LOCAL, account_id
    from frontend.duckyos_account import _load_blob

    account = account_id()
    own = str(_load_blob().get("device_key_id") or "").strip()
    if account == LOCAL or not own:
        raise LinkError("Sign in to your Ducky account on this UEFN Ducky first.")
    key = data_crypto.adk(account)
    if key is None:
        raise LinkError("Waiting for your account's key. Try again in a minute.")
    return account, own, key


def list_duckies() -> dict[str, Any]:
    """The account's UEFN Duckies (this one marked ``mine``); ``live`` = reachable now."""
    from frontend.duckyos_account import list_account_pcs

    return list_account_pcs()


def call(to: str, method: str, args: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict[str, Any]:
    """Run ``method`` on the UEFN Ducky ``to`` (its key id). ``{"ok", "result" | "error"}``; never raises."""
    from frontend.duckyos_account import RPC_ALLOWLIST, DuckyOSAccountError, _plugin_collect

    to = str(to or "").strip()
    if not _KEY_ID.fullmatch(to):
        return {"ok": False, "error": "Pick one of your UEFN Duckies."}
    if method not in RPC_ALLOWLIST:
        return {"ok": False, "error": "That call can't go to another UEFN Ducky."}
    try:
        account, own, key = _context()
    except LinkError as exc:
        return {"ok": False, "error": str(exc)}
    if to == own:
        return {"ok": False, "error": "That's this UEFN Ducky."}
    from frontend import remote_trust

    link = b""
    if method not in PAIRING:
        peer = remote_trust.pinned_pc(to)
        if not peer:
            return _ask_to_pair(to)
        link = pc_link_key(peer)
    rid = uuid.uuid4().hex
    sealed = seal(args or {}, adk=key, key=link, aad=_aad(account, own, to, method, rid, "req"))
    if link:
        sealed["pub"] = remote_trust.pc_pub_b64()
    if len(sealed["sealed"]) > SEALED_MAX:
        return {"ok": False, "error": "That request is too big to send."}
    errors = {"unavailable_code": "link_unavailable", "unavailable_msg": "", "error_code": "link_failed"}
    try:
        _plugin_collect("uefn-ducky", "desktop-rpc", {"keyId": to, "id": rid, "method": method, "args": sealed}, timeout=15.0, **errors)
        deadline = time.monotonic() + max(1.0, float(timeout))
        pause = 0.3
        while True:
            row = _plugin_collect("uefn-ducky", "desktop-rpc-result", {"id": rid, "keyId": to}, timeout=15.0, **errors)
            status = str(row.get("status") or "")
            if status not in ("", "pending"):
                break
            if time.monotonic() >= deadline:
                return {"ok": False, "error": NOT_ANSWERING}
            time.sleep(pause)
            pause = min(pause * 1.5, 1.0)
    except DuckyOSAccountError as exc:
        return {"ok": False, "error": exc.message}
    if status == "gone":
        return {"ok": False, "error": NOT_ANSWERING}
    if row.get("ok") is False:
        return {"ok": False, "error": str(row.get("error") or "That UEFN Ducky couldn't do it.")}
    result = row.get("result")
    if result is None:
        return {"ok": True, "result": None}
    try:
        opened = open_sealed(result, adk=key, key=link, aad=_aad(account, own, to, method, rid, "res"))
        return {"ok": True, "result": opened}
    except (LinkError, ValueError, zlib.error) as exc:
        return {"ok": False, "error": str(exc)}


def _ask_to_pair(to: str) -> dict[str, Any]:
    """First call to a PC: ask it to approve this one, pin its key, say what to do."""
    from frontend import remote_trust

    asked = call(to, "pair_request", {"pub": remote_trust.pc_pub_b64(), "label": "UEFN Ducky PC"})
    got = asked.get("result") if isinstance(asked.get("result"), dict) else {}
    if not (asked.get("ok") and got.get("pcPub")):
        return {"ok": False, "error": str(asked.get("error") or NOT_ANSWERING)}
    try:
        remote_trust.pin_pc(to, str(got["pcPub"]))
    except remote_trust.TrustError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "pair": True,
            "error": f"Approve this PC on the other UEFN Ducky (code {got.get('code', '')}), then try again."}


def answer(rid: str, sender: str, method: str, args: Any) -> dict[str, Any]:
    """The mailbox waiter's answer to another UEFN Ducky's sealed request:
    open it, run it like any mailbox call, seal what goes back."""
    from frontend.duckyos_account import dispatch_desktop_rpc

    from frontend import remote_trust

    link = b""
    try:
        account, own, key = _context()
        if method not in PAIRING:
            # Anything but pairing needs a PC approved in this window (the ADK is the site's too).
            pub = str(args.get("pub") or "") if isinstance(args, dict) else ""
            if not pub or remote_trust.pub_id(pub) not in remote_trust.approved():
                return {"ok": False, "error": "Approve that PC in this UEFN Ducky's window first."}
            link = pc_link_key(pub)
        opened = open_sealed(args, adk=key, key=link, aad=_aad(account, sender, own, method, rid, "req"))
    except (LinkError, ValueError, zlib.error) as exc:
        return {"ok": False, "error": str(exc)}
    out = dispatch_desktop_rpc(method, opened if isinstance(opened, dict) else {})
    if "result" in out:
        sealed = seal(out["result"], adk=key, key=link, aad=_aad(account, sender, own, method, rid, "res"))
        if len(sealed["sealed"]) > SEALED_MAX:
            return {"ok": False, "error": "The answer is too big to send back. Ask for less."}
        out = {**out, "result": sealed}
    return out
