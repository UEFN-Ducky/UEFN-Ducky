"""DPAPI-encrypted API key storage (Windows). Non-Windows keeps secrets in memory only."""

from __future__ import annotations

import json
import os
import struct
import sys
import time
from pathlib import Path
from typing import Any

from frontend.settings import default_app_data_dir

_MAGIC = b"UMCP"
_VERSION = 1
_ENTROPY = b"UEFN-Ducky-v1-credentials"

_memory_cache: dict[str, str] | None = None


def credentials_path() -> Path:
    return default_app_data_dir() / "credentials.dat"


def _dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi(data: bytes, entropy: bytes | None, *, protect: bool) -> bytes:
    """One CryptProtectData / CryptUnprotectData call. Windows hands the result back in a
    LocalAlloc buffer the caller must LocalFree: kept, every sealed chat save and read
    leaked its whole ciphertext or plaintext into the process heap (GBs an hour)."""
    if not _dpapi_available():
        raise OSError("Credential persist requires Windows DPAPI")
    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.c_void_p),
        ]

    def _blob(raw: bytes) -> tuple[DATA_BLOB, Any]:
        buf = ctypes.create_string_buffer(raw, len(raw))  # one copy, not one int per byte
        blob = DATA_BLOB()
        blob.cbData = len(raw)
        blob.pbData = ctypes.addressof(buf)  # cast() would tie buf into a cycle only gc frees
        return blob, buf  # the buffer must outlive the call

    in_blob, _in_buf = _blob(data)
    # entropy None: plain user DPAPI, the way Chromium seals its cookie key.
    ent_blob, _ent_buf = _blob(_ENTROPY + entropy) if entropy is not None else (None, None)
    out_blob = DATA_BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob) if ent_blob is not None else None,
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    ):
        raise OSError("CryptProtectData failed" if protect else "CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        local_free = ctypes.windll.kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free(out_blob.pbData)


def _protect(plaintext: bytes, entropy: bytes = b"") -> bytes:
    return _dpapi(plaintext, entropy, protect=True)


def _unprotect(ciphertext: bytes, entropy: bytes = b"") -> bytes:
    return _dpapi(ciphertext, entropy, protect=False)


def protect_text(value: str) -> bytes:
    """DPAPI-encrypt one secret value (per-row storage, ADR 0003)."""
    return _MAGIC + struct.pack("B", _VERSION) + _protect(value.encode("utf-8"))


def unprotect_text(blob: bytes) -> str:
    if len(blob) < 5 or blob[:4] != _MAGIC or blob[4] != _VERSION:
        raise ValueError("Invalid secret blob")
    return _unprotect(blob[5:]).decode("utf-8")


def protect_bytes(data: bytes, entropy: bytes) -> bytes:
    """DPAPI for this Windows user, plus ``entropy`` as a second secret (e.g. an
    account data key): both are needed to decrypt."""
    return _protect(data, entropy)


def unprotect_bytes(blob: bytes, entropy: bytes) -> bytes:
    """Inverse of :func:`protect_bytes`; raises ``OSError`` on the wrong entropy."""
    return _unprotect(blob, entropy)


def protect_plain(data: bytes) -> bytes:
    """User DPAPI with no entropy (what Chromium's ``os_crypt.encrypted_key`` holds)."""
    return _dpapi(data, None, protect=True)


def unprotect_plain(blob: bytes) -> bytes:
    return _dpapi(blob, None, protect=False)


def _use_db() -> bool:
    from backend.store.switch import use_db

    return use_db("secrets")


def _serialize_keys(keys: dict[str, str]) -> bytes:
    payload = json.dumps(keys, separators=(",", ":")).encode("utf-8")
    return _MAGIC + struct.pack("B", _VERSION) + _protect(payload)


def _deserialize_keys(data: bytes) -> dict[str, str]:
    if len(data) < 5 or data[:4] != _MAGIC:
        raise ValueError("Invalid credentials file")
    version = data[4]
    if version != _VERSION:
        raise ValueError(f"Unsupported credentials version: {version}")
    raw = _unprotect(data[5:])
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("Invalid credentials payload")
    return {str(k): str(v) for k, v in parsed.items() if v}


def load_keys(*, use_cache: bool = True) -> dict[str, str]:
    global _memory_cache
    if use_cache and _memory_cache is not None:
        return dict(_memory_cache)
    if not _dpapi_available():
        # ponytail: Windows-only persist; tests/dev keep the process cache.
        _memory_cache = {} if _memory_cache is None else _memory_cache
        return dict(_memory_cache)
    if _use_db():
        try:
            from backend.store.importers import phase1
            from backend.store.repos import secrets as repo

            phase1.ensure("secrets")
            keys = {}
            for name, blob in repo.all_blobs().items():
                try:
                    value = unprotect_text(blob)
                except (OSError, ValueError):
                    continue
                if value:
                    keys[name] = value
            _memory_cache = keys
            return dict(keys)
        except (OSError, RuntimeError):
            pass
    path = credentials_path()
    if not path.is_file():
        _memory_cache = {}
        return {}
    try:
        keys = _deserialize_keys(path.read_bytes())
    except (OSError, ValueError, json.JSONDecodeError):
        keys = {}
    _memory_cache = keys
    return dict(keys)


def save_keys(keys: dict[str, str]) -> None:
    global _memory_cache
    cleaned = {k: v.strip() for k, v in keys.items() if v and v.strip()}
    _memory_cache = dict(cleaned)
    if not _dpapi_available():
        return
    if _use_db():
        try:
            from backend.store.importers import phase1
            from backend.store.repos import secrets as repo

            phase1.ensure("secrets")
            current = repo.all_blobs()
            for name in set(current) - set(cleaned):
                repo.delete(name)
            for name, value in cleaned.items():
                blob = current.get(name)
                if blob is not None:
                    try:
                        if unprotect_text(blob) == value:
                            continue
                    except (OSError, ValueError):
                        pass
                repo.set_blob(name, protect_text(value))
            return
        except (OSError, RuntimeError):
            pass
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(_serialize_keys(cleaned))
    os.replace(tmp, path)


def set_key(provider: str, value: str) -> None:
    keys = load_keys(use_cache=False)
    v = value.strip()
    if v:
        keys[provider] = v
    else:
        keys.pop(provider, None)
    save_keys(keys)


def clear_key(provider: str) -> None:
    keys = load_keys(use_cache=False)
    keys.pop(provider, None)
    save_keys(keys)


def get_key(provider: str) -> str | None:
    return load_keys().get(provider)


_FRESH: dict[str, tuple[bytes, str]] = {}


def get_key_fresh(provider: str) -> str | None:
    """One key as stored now, not as this process cached it at start.

    The MCP bridge is its own process: after the app signs in as another DuckyOS
    account, the bridge's next call must act as the new account (plan rule 0.6).
    One row read per call; DPAPI decrypts only when the stored blob changed.
    ponytail: the files backend / non-Windows keep the process cache.
    """
    if not (_dpapi_available() and _use_db()):
        return get_key(provider)
    try:
        from backend.store.importers import phase1
        from backend.store.repos import secrets as repo

        phase1.ensure("secrets")
        blob = repo.get_blob(provider)
    except (OSError, RuntimeError):
        return get_key(provider)
    if blob is None:
        _FRESH.pop(provider, None)
        return None
    hit = _FRESH.get(provider)
    if hit and hit[0] == blob:
        return hit[1]
    try:
        value = unprotect_text(blob)
    except (OSError, ValueError):
        return None
    _FRESH[provider] = (blob, value)
    return value


# ((db path, db generation, login writes in this process), stamp, monotonic read time).
# Every chat save asks who is signed in; the meta read behind it cost 33 ms in the slow 5%
# of in-reply checkpoints under load. A login written here changes the key at once; one
# written by another process (the app, seen from a bridge) is seen within STAMP_TTL_S.
_STAMP: tuple[tuple[str, int, int], tuple[str, str], float] | None = None
STAMP_TTL_S = 1.0


def login_stamp() -> tuple[str, str] | None:
    """``(ducky.db path, meta login_version)``: changes whenever the login row is written,
    in any process. None when keys don't live in ducky.db (then nothing may be cached)."""
    global _STAMP
    if not (_dpapi_available() and _use_db()):
        return None
    try:
        from backend.store import db
        from backend.store.importers import phase1
        from backend.store.repos import secrets as repo

        phase1.ensure("secrets")
        memo = (str(db.db_path()), db.generation(), repo.login_writes())
        now = time.monotonic()
        hit = _STAMP
        if hit is not None and hit[0] == memo and now - hit[2] < STAMP_TTL_S:
            return hit[1]
        stamp = (memo[0], repo.login_version())
        _STAMP = (memo, stamp, now)
        return stamp
    except (OSError, RuntimeError):
        return None


def forget_login_stamp() -> None:
    global _STAMP
    _STAMP = None


def has_key(provider: str) -> bool:
    return bool(get_key(provider))


def clear_memory_cache() -> None:
    global _memory_cache
    _memory_cache = None


def reject_api_key_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Strip accidental *_api_key fields from panel settings JSON."""
    banned = ("anthropic_api_key", "openai_api_key", "google_api_key", "api_key", "gemini_api_key")
    return {k: v for k, v in data.items() if k not in banned and not k.endswith("_api_key")}
