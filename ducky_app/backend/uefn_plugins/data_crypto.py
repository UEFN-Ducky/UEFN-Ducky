"""Plugin data encrypted for the signed-in account (plan §13).

Every per-account store (plugin cache + prefs rows, plugin docs, scoped files,
automations) is sealed with DPAPI (this Windows user) plus the account data key
(ADK) as a second secret: two accounts on one Windows user can't read each other's
data, not even from the DB or the files directly.

- The ADK comes from the server (uefn-ducky ``account-data-key``, same value every
  sign-in). It lives in memory; its disk cache (offline restarts) is DPAPI-wrapped
  with the account's device token, and is deleted whenever the login changes
  (sign-out, expiry, unpair: :func:`on_login_change`, called by the login funnel).
- Signed out (``_local``) there is no ADK: plain DPAPI.
- No ADK yet (offline first sign-in): the account's data is locked, never written
  as plaintext; scopes open read-only until the key arrives.
- Team copies are encrypted again with each team's own key before they leave the
  PC (:mod:`team_keys`); this module is the at-rest layer on this PC.
"""

from __future__ import annotations

import base64
import functools
import json
import threading
import time
from typing import Any

PREFIX = "enc1:"
FILE_MAGIC = b"UDE1"
FETCH_RETRY_S = 60.0
_LOCAL = "_local"
_CACHE_NAME = "account_data_key.bin"

_KEYS: dict[str, bytes] = {}  # account id -> ADK, this process only
_LAST_TRY: dict[str, float] = {}
_MIGRATED: set[str] = set()
_GUARD = threading.RLock()


class Locked(Exception):
    """The signed-in account's data key isn't on this PC yet. Deliberately not an
    ``OSError``: no "disk failed, try the file fallback" path may swallow it."""


# --------------------------------------------------------------------------- the key


def _cache_path():
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir() / _CACHE_NAME


def _fetch_adk() -> bytes:
    """The signed-in account's key from the server (tests replace this)."""
    from frontend.duckyos_account import _plugin_collect

    out = _plugin_collect(
        "uefn-ducky", "account-data-key", {},
        unavailable_code="adk_unavailable", unavailable_msg="", error_code="adk_error", timeout=10.0,
    )
    key = base64.b64decode(str(out.get("key") or ""), validate=True)
    if int(out.get("version") or 0) != 1 or len(key) != 32:
        raise ValueError("unexpected account data key")
    return key


def _device_token() -> str:
    from frontend.duckyos_account import _load_blob

    return str(_load_blob().get("device_key") or "")


def _read_cache(account: str) -> bytes | None:
    from backend.agent.secrets import unprotect_bytes

    token = _device_token()
    try:
        doc = json.loads(_cache_path().read_text(encoding="utf-8"))
        if not token or doc.get("account") != account:
            return None
        key = unprotect_bytes(base64.b64decode(doc["wrapped"]), b"device:" + token.encode("utf-8"))
        return key if len(key) == 32 else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(account: str, key: bytes) -> None:
    from backend.agent.secrets import protect_bytes

    token = _device_token()
    if not token:
        return  # ponytail: session-only logins keep the key in memory; the next start fetches it
    wrapped = protect_bytes(key, b"device:" + token.encode("utf-8"))
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"account": account, "wrapped": base64.b64encode(wrapped).decode("ascii")}), "utf-8")


def adk(account: str) -> bytes | None:
    """The data key of ``account`` if it is the signed-in one: memory, then the disk
    cache, then the server (at most one try a minute). ``None`` = locked for now."""
    from backend.uefn_plugins.scopes import account_id

    if account == _LOCAL or account != account_id():
        return None
    with _GUARD:
        key = _KEYS.get(account) or _read_cache(account)
        if key is None and time.monotonic() - _LAST_TRY.get(account, -FETCH_RETRY_S) >= FETCH_RETRY_S:
            _LAST_TRY[account] = time.monotonic()
            try:
                key = _fetch_adk()
                _write_cache(account, key)
            except Exception:
                key = None
        if key is not None:
            _KEYS[account] = key
    return key


def available(account: str) -> bool:
    return account == _LOCAL or adk(account) is not None


def migrate_if_ready(account: str) -> None:
    """Seal the account's old plaintext rows as soon as its key is here (once per process)."""
    if account not in _MIGRATED and available(account):
        ensure_migrated(account)


def prefetch() -> None:
    """Sign-in: fetch (and cache) the new account's key off the UI thread, then seal
    its old plaintext rows, so the first plugin call doesn't wait."""
    def _work() -> None:
        try:
            from backend.uefn_plugins.scopes import account_id

            account = account_id()
            if account != _LOCAL and adk(account) is not None:
                ensure_migrated(account)
        except Exception:
            pass

    threading.Thread(target=_work, name="account-data-key", daemon=True).start()


def on_login_change(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Login funnel hook (``duckyos_account._save_blob`` / ``_clear_blob``): a new
    account, sign-out, expiry or unpair drops the key cache wrapped with the old
    device token, and every key but the new account's."""
    from frontend.duckyos_account import account_key

    if account_key(before) == account_key(after) and before.get("device_key") == after.get("device_key"):
        return
    with _GUARD:
        try:
            _cache_path().unlink(missing_ok=True)
        except OSError:
            pass
        from backend.uefn_plugins.scopes import account_id_for

        keep = account_id_for(account_key(after))
        for acct in [a for a in _KEYS if a != keep]:
            del _KEYS[acct]
        _LAST_TRY.clear()
        _open.cache_clear()
        _open_big.cache_clear()
    from backend.uefn_plugins import team_keys

    team_keys.on_login_change(keep)


# --------------------------------------------------------------------------- seal / open


def _entropy(account: str) -> bytes:
    if account == _LOCAL:
        entropy = b"local"
    else:
        key = adk(account)
        if key is None:
            raise Locked("Waiting for this account's data key. Plugin data opens once you're online.")
        entropy = b"adk:" + key
    ensure_migrated(account)
    return entropy


def is_sealed(stored: str | None) -> bool:
    return isinstance(stored, str) and stored.startswith(PREFIX)


def seal_text(text: str, account: str) -> str:
    from backend.agent.secrets import protect_bytes

    return PREFIX + base64.b64encode(protect_bytes(text.encode("utf-8"), _entropy(account))).decode("ascii")


# Sealed rows longer than this are big docs, memoized apart from the small rows.
_BIG_CHARS = 64 * 1024


def _unseal(stored: str, entropy: bytes) -> str:
    from backend.agent.secrets import unprotect_bytes

    return unprotect_bytes(base64.b64decode(stored[len(PREFIX):]), entropy).decode("utf-8")


@functools.lru_cache(maxsize=512)
def _open(stored: str, entropy: bytes) -> str:
    # ponytail: memo by ciphertext (DPAPI salts every write, so a hit is the same row
    # unchanged); ~0.3 ms per DPAPI call otherwise, 512 small rows max.
    return _unseal(stored, entropy)


@functools.lru_cache(maxsize=4)
def _open_big(stored: str, entropy: bytes) -> str:
    # A big doc is rewritten whole on every save, each save a new ciphertext: a few
    # slots keep the current versions quick to read (a 500 KB doc takes ~80 ms to
    # unseal) without keeping every old version and its plaintext until restart.
    return _unseal(stored, entropy)


def open_text(stored: str, account: str) -> str:
    """Plaintext of a sealed value; raises :class:`Locked` or ``OSError`` (wrong key)."""
    entropy = _entropy(account)
    if len(stored) > _BIG_CHARS:
        return _open_big(stored, entropy)
    return _open(stored, entropy)


def seal_bytes(data: bytes, account: str) -> bytes:
    from backend.agent.secrets import protect_bytes

    return FILE_MAGIC + protect_bytes(data, _entropy(account))


def open_bytes(raw: bytes, account: str) -> bytes:
    from backend.agent.secrets import unprotect_bytes

    if not raw.startswith(FILE_MAGIC):
        raise ValueError("file is not sealed")
    return unprotect_bytes(raw[len(FILE_MAGIC):], _entropy(account))


# --------------------------------------------------------------------------- one-time migration


def ensure_migrated(account: str) -> None:
    """Once per account per process: the account's plaintext plugin cache/prefs rows
    (from before encryption) are sealed in place and verified."""
    if account in _MIGRATED:
        return
    with _GUARD:
        if account in _MIGRATED:
            return
        _MIGRATED.add(account)
    from backend.agent.secrets import unprotect_text
    from backend.store.repos import plugin_kv

    for plugin_id, scope, key, value, encrypted in plugin_kv.unsealed_rows(account, PREFIX):
        try:
            text = unprotect_text(base64.b64decode(value)) if encrypted else value
            json.loads(text)
        except (OSError, ValueError):
            continue  # unreadable legacy row: left alone, never guessed at
        sealed = seal_text(text, account)
        if open_text(sealed, account) != text:
            raise RuntimeError("plugin data migration failed its check")
        plugin_kv.replace_value(plugin_id, key, sealed, account=account, scope=scope, old=value)


def reset_for_tests() -> None:
    from backend.uefn_plugins import team_keys

    with _GUARD:
        _KEYS.clear()
        _LAST_TRY.clear()
        _MIGRATED.clear()
        _open.cache_clear()
        _open_big.cache_clear()
    team_keys.reset_for_tests()
