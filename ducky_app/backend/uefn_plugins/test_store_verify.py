"""The download tamper check: a check that errors refuses the install, never skips it."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from backend.store import db
from backend.uefn_plugins import signing, store


def _zip(pid: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps({"id": pid, "kind": "plugin", "version": "1.0.0", "label": pid}))
        zf.writestr("backend/__init__.py", "def register(api):\n    pass\n")
    return buf.getvalue()


@pytest.fixture
def signed_store(tmp_path, monkeypatch: pytest.MonkeyPatch):
    for var in ("LOCALAPPDATA", "USERPROFILE", "HOME"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setattr(store, "appdata_dir", lambda: tmp_path / "app")
    monkeypatch.setattr(signing, "STORE_SIGNING_PUBKEY", "test-key")  # signing is live
    monkeypatch.setattr(signing, "verify_signature", lambda record, sig, *_a: sig == "good")
    db.reset_for_tests()
    yield
    db.reset_for_tests()


def _record(data: bytes, signature: str) -> dict:
    return {"slug": "verifydemo", "sha256": hashlib.sha256(data).hexdigest(), "signature": signature}


def test_an_error_in_the_check_refuses_the_install(signed_store) -> None:
    out = store.import_plugin_from_bytes(_zip("verifydemo"), source="store", signed_record="yes")  # type: ignore[arg-type]
    assert out == {"ok": False, "error": "Could not verify this download. Try again."}
    assert not store.plugin_dir("verifydemo").exists()


def test_a_good_signature_installs(signed_store) -> None:
    data = _zip("verifydemo")
    out = store.import_plugin_from_bytes(data, source="store", signed_record=_record(data, "good"))
    assert out.get("ok"), out
    assert (store.plugin_dir("verifydemo") / "plugin.json").is_file()


def test_a_bad_signature_is_refused_as_before(signed_store) -> None:
    data = _zip("verifydemo")
    out = store.import_plugin_from_bytes(data, source="store", signed_record=_record(data, "forged"))
    assert out == {"ok": False, "error": "This download's signature is invalid — refusing to install."}
    assert not store.plugin_dir("verifydemo").exists()


def test_a_local_draft_without_a_record_installs_as_before(signed_store) -> None:
    out = store.import_plugin_from_bytes(_zip("verifydemo"), source="local", signed_record=None)
    assert out.get("ok"), out
    assert (store.plugin_dir("verifydemo") / "plugin.json").is_file()
