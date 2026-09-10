"""DPAPI-encrypted API key storage (Windows). Non-Windows keeps secrets in memory only."""

from __future__ import annotations

import json
import os
import struct
import sys
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


def _protect(plaintext: bytes) -> bytes:
    if not _dpapi_available():
        raise OSError("Credential persist requires Windows DPAPI")
    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    def _bytes_to_blob(data: bytes) -> DATA_BLOB:
        buf = (ctypes.c_byte * len(data))(*data)
        blob = DATA_BLOB()
        blob.cbData = len(data)
        blob.pbData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
        return blob

    in_blob = _bytes_to_blob(plaintext)
    ent_blob = _bytes_to_blob(_ENTROPY)
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob),
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    ):
        raise OSError("CryptProtectData failed")
    return bytes(ctypes.string_at(out_blob.pbData, out_blob.cbData))


def _unprotect(ciphertext: bytes) -> bytes:
    if not _dpapi_available():
        raise OSError("Credential persist requires Windows DPAPI")
    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    def _bytes_to_blob(data: bytes) -> DATA_BLOB:
        buf = (ctypes.c_byte * len(data))(*data)
        blob = DATA_BLOB()
        blob.cbData = len(data)
        blob.pbData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
        return blob

    in_blob = _bytes_to_blob(ciphertext)
    ent_blob = _bytes_to_blob(_ENTROPY)
    out_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob),
        None,
        None,
        0x01,
        ctypes.byref(out_blob),
    ):
        raise OSError("CryptUnprotectData failed")
    return bytes(ctypes.string_at(out_blob.pbData, out_blob.cbData))


def protect_text(value: str) -> bytes:
    """DPAPI-encrypt one secret value (per-row storage, ADR 0003)."""
    return _MAGIC + struct.pack("B", _VERSION) + _protect(value.encode("utf-8"))


def unprotect_text(blob: bytes) -> str:
    if len(blob) < 5 or blob[:4] != _MAGIC or blob[4] != _VERSION:
        raise ValueError("Invalid secret blob")
    return _unprotect(blob[5:]).decode("utf-8")


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


def has_key(provider: str) -> bool:
    return bool(get_key(provider))


def clear_memory_cache() -> None:
    global _memory_cache
    _memory_cache = None


def reject_api_key_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Strip accidental *_api_key fields from panel settings JSON."""
    banned = ("anthropic_api_key", "openai_api_key", "google_api_key", "api_key", "gemini_api_key")
    return {k: v for k, v in data.items() if k not in banned and not k.endswith("_api_key")}
