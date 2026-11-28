"""Sealed values: what one backend seals for this OS user and an account, the other opens."""

from __future__ import annotations

import base64

import pytest

from backend.agent.secrets import protect_bytes, unprotect_bytes
from backend.uefn_plugins import data_crypto

_TEXT = 'héllo 🦆 "quoted"\nline two'
_LOCAL = b"local"  # data_crypto._entropy signed out
_ACCOUNT = b"adk:" + bytes(range(32))  # data_crypto._entropy with an account data key


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@pytest.mark.parametrize("entropy", [_LOCAL, _ACCOUNT], ids=["signed-out", "account"])
def test_text_sealed_by_python_opens_in_rust(rust_store, entropy) -> None:
    stored = data_crypto.PREFIX + _b64(protect_bytes(_TEXT.encode("utf-8"), entropy))
    assert rust_store.store("seal", "open_text", stored=stored, entropy=_b64(entropy)) == _TEXT


@pytest.mark.parametrize("entropy", [_LOCAL, _ACCOUNT], ids=["signed-out", "account"])
def test_text_sealed_by_rust_opens_in_python(rust_store, entropy) -> None:
    stored = rust_store.store("seal", "seal_text", text=_TEXT, entropy=_b64(entropy))
    assert stored.startswith(data_crypto.PREFIX) and "llo" not in stored
    assert data_crypto._unseal(stored, entropy) == _TEXT
    # Sealed again it is another ciphertext: nothing tells two equal values apart.
    assert rust_store.store("seal", "seal_text", text=_TEXT, entropy=_b64(entropy)) != stored


def test_the_signed_out_account_goes_through_pythons_own_calls(rust_store) -> None:
    by_python = data_crypto.seal_text(_TEXT, "_local")
    assert rust_store.store("seal", "open_text", stored=by_python, entropy=_b64(_LOCAL)) == _TEXT
    by_rust = rust_store.store("seal", "seal_text", text=_TEXT, entropy=_b64(_LOCAL))
    assert data_crypto.open_text(by_rust, "_local") == _TEXT


def test_bytes_sealed_by_one_open_in_the_other(rust_store) -> None:
    data = bytes(range(256)) * 3
    by_python = data_crypto.seal_bytes(data, "_local")
    assert rust_store.store("seal", "open_bytes", raw=_b64(by_python), entropy=_b64(_LOCAL)) == _b64(data)
    by_rust = base64.b64decode(rust_store.store("seal", "seal_bytes", data=_b64(data), entropy=_b64(_ACCOUNT)))
    assert by_rust.startswith(data_crypto.FILE_MAGIC)
    assert unprotect_bytes(by_rust[len(data_crypto.FILE_MAGIC):], _ACCOUNT) == data


def test_a_big_document_round_trips(rust_store) -> None:
    text = "ducks é " * 40_000  # 320 KB, past data_crypto._BIG_CHARS
    stored = rust_store.store("seal", "seal_text", text=text, entropy=_b64(_ACCOUNT))
    assert data_crypto._unseal(stored, _ACCOUNT) == text
    by_python = data_crypto.PREFIX + _b64(protect_bytes(text.encode("utf-8"), _ACCOUNT))
    assert rust_store.store("seal", "open_text", stored=by_python, entropy=_b64(_ACCOUNT)) == text


def test_another_accounts_key_opens_nothing(rust_store) -> None:
    other = b"adk:" + bytes(32)
    by_python = data_crypto.PREFIX + _b64(protect_bytes(_TEXT.encode("utf-8"), _ACCOUNT))
    for wrong in (other, _LOCAL):
        status, reply = rust_store.request("/__rs/store/seal/open_text", {"stored": by_python, "entropy": _b64(wrong)})
        assert status == 200 and reply["ok"] is False and _TEXT not in str(reply)
    by_rust = rust_store.store("seal", "seal_text", text=_TEXT, entropy=_b64(_ACCOUNT))
    with pytest.raises(OSError):
        data_crypto._unseal(by_rust, other)
