"""Team data keys: each team's plugin data is encrypted with that team's own key.

- The key comes from the Store (``team-data-key``, members only; the same value
  every time). Core signs at most 30 keys a minute per site, so it is kept: in
  memory, and on disk DPAPI-wrapped with the device token, per account (like the
  account data key in :mod:`data_crypto`). A login change drops the file and every
  other account's keys; losing access to a team drops that team's key.
- An object is ``UDT1`` + key version (1 byte) + 12-byte nonce + AES-256-GCM
  ciphertext||tag, with the 5-byte header as associated data. The website opens
  the same bytes with WebCrypto.
- The nonce is derived from the item and its plaintext (HMAC under a key derived
  from the team key), so the same item and bytes always encrypt to the same
  object: a push names the uploaded bytes' size and sha256, and the upload that
  follows re-reads and re-encrypts to exactly those bytes. Two different
  plaintexts never share a nonce.
- Objects from before team keys are plaintext and still read.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from typing import Any, Callable

MAGIC = b"UDT1"
HEADER_LEN = 5
NONCE_LEN = 12
TAG_LEN = 16
OVERHEAD = HEADER_LEN + NONCE_LEN + TAG_LEN
FETCH_RETRY_S = 60.0
_CACHE_NAME = "team_data_keys.json"

_KEYS: dict[tuple[str, str], tuple[int, bytes]] = {}  # (account, team) -> (version, key)
_LAST_TRY: dict[tuple[str, str], float] = {}
_GUARD = threading.RLock()


# --------------------------------------------------------------------------- the key


def _cache_path():
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir() / _CACHE_NAME


def _entropy(team: str) -> bytes | None:
    from frontend.duckyos_account import _load_blob

    token = str(_load_blob().get("device_key") or "")
    return (b"device:" + token.encode("utf-8") + b"|team:" + team.encode("utf-8")) if token else None


def _read_disk(account: str) -> dict[str, Any]:
    try:
        doc = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("account") != account or not isinstance(doc.get("teams"), dict):
        return {}
    return doc["teams"]


def _from_disk(account: str, team: str) -> tuple[int, bytes] | None:
    from backend.agent.secrets import unprotect_bytes

    entry = _read_disk(account).get(team)
    entropy = _entropy(team)
    if not isinstance(entry, dict) or entropy is None:
        return None
    try:
        key = unprotect_bytes(base64.b64decode(entry["wrapped"]), entropy)
        version = int(entry["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return (version, key) if len(key) == 32 and 1 <= version <= 255 else None


def _write_disk(account: str, team: str, entry: tuple[int, bytes] | None) -> None:
    """Store (or with ``None`` drop) one team's wrapped key in the account's file."""
    from backend.agent.secrets import protect_bytes

    teams = dict(_read_disk(account))
    if entry is None:
        if team not in teams:
            return
        teams.pop(team)
    else:
        entropy = _entropy(team)
        if entropy is None:
            return  # ponytail: session-only logins keep the key in memory; the next start fetches it
        teams[team] = {"version": entry[0], "wrapped": base64.b64encode(protect_bytes(entry[1], entropy)).decode("ascii")}
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"account": account, "teams": teams}), "utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _parse(out: dict[str, Any]) -> tuple[int, bytes]:
    key = base64.b64decode(str(out.get("key") or ""), validate=True)
    version = int(out.get("version") or 0)
    if not 1 <= version <= 255 or len(key) != 32:
        raise ValueError("unexpected team data key")
    return version, key


def get(account: str, team: str, fetch: Callable[[], dict[str, Any]]) -> tuple[int, bytes] | None:
    """``(version, key)`` of ``team`` for ``account``: memory, then the disk cache,
    then ``fetch()`` (the Store collect; at most one answered try a minute per
    team). ``None`` = no key for now. A fetch error (offline, removed from the
    team) is raised to the caller, which decides what it means; one marked
    ``offline`` doesn't count as a try."""
    slot = (account, team)
    with _GUARD:
        got = _KEYS.get(slot) or _from_disk(account, team)
        if got is not None:
            _KEYS[slot] = got
            return got
        if time.monotonic() - _LAST_TRY.get(slot, -FETCH_RETRY_S) < FETCH_RETRY_S:
            return None
        _LAST_TRY[slot] = time.monotonic()
    try:
        got = _parse(fetch())  # outside the lock: another team's round never waits on this one
    except Exception as exc:
        if getattr(exc, "offline", False):
            # Never reached the signer: back online, the next round asks again at once.
            with _GUARD:
                _LAST_TRY.pop(slot, None)
        raise
    with _GUARD:
        _KEYS[slot] = got
        _write_disk(account, team, got)
    return got


def forget(account: str, team: str) -> None:
    """Access to the team is lost: its key goes from memory and disk."""
    with _GUARD:
        _KEYS.pop((account, team), None)
        _LAST_TRY.pop((account, team), None)
        _write_disk(account, team, None)


def on_login_change(keep_account: str) -> None:
    """A new account, sign-out, expiry or unpair: the file wrapped with the old
    device token goes, and every key but the new account's."""
    with _GUARD:
        try:
            _cache_path().unlink(missing_ok=True)
        except OSError:
            pass
        for slot in [s for s in _KEYS if s[0] != keep_account]:
            del _KEYS[slot]
        _LAST_TRY.clear()


def reset_for_tests() -> None:
    with _GUARD:
        _KEYS.clear()
        _LAST_TRY.clear()


# --------------------------------------------------------------------------- objects


def _nonce(key: bytes, item: tuple[str, str, str], data: bytes) -> bytes:
    sub = hmac.new(key, b"uefn-ducky/tdk-nonce", hashlib.sha256).digest()
    msg = "\n".join(item).encode("utf-8") + b"\n" + hashlib.sha256(data).digest()
    return hmac.new(sub, msg, hashlib.sha256).digest()[:NONCE_LEN]


def seal(entry: tuple[int, bytes], item: tuple[str, str, str], data: bytes) -> bytes:
    """``data`` of ``item`` (kind, plugin id, key) as the object to upload."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    version, key = entry
    header = MAGIC + bytes([version])
    nonce = _nonce(key, item, data)
    return header + nonce + AESGCM(key).encrypt(nonce, data, header)


def is_sealed(raw: bytes) -> bool:
    return len(raw) >= OVERHEAD and raw[:4] == MAGIC


def open_object(entry: tuple[int, bytes], raw: bytes, enc: int | None = None) -> bytes:
    """Plaintext of a downloaded object. ``enc`` is the key version the server
    recorded (0 = plaintext from before team keys); ``None`` when it didn't say,
    then the header decides. Raises ``ValueError`` when it can't be opened."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if enc == 0 or (enc is None and not is_sealed(raw)):
        return raw
    version, key = entry
    if not is_sealed(raw):
        raise ValueError("object is not encrypted")
    if raw[4] != version:
        raise ValueError(f"object needs team key version {raw[4]}")
    header = raw[:HEADER_LEN]
    try:
        return AESGCM(key).decrypt(raw[HEADER_LEN:HEADER_LEN + NONCE_LEN], raw[HEADER_LEN + NONCE_LEN:], header)
    except InvalidTag as exc:
        raise ValueError("object failed its integrity check") from exc
