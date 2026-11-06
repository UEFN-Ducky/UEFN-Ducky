from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from backend.uefn_plugins import signing


def _key():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv, base64.b64encode(pub).decode()


def _install(root, slug, priv, *, record_slug=None, tamper=False):
    folder = root / slug
    folder.mkdir(parents=True)
    record = {"compiled": True, "issued_at": 1793930932, "sha256": "ab" * 32,
              "slug": record_slug or slug, "team_id": "", "version": "1.1.61", "visibility": "public"}
    record["signature"] = base64.b64encode(priv.sign(signing.canonical_message(record))).decode()
    if tamper:
        record["version"] = "9.9.9"
    (folder / signing.SIG_FILENAME).write_text(json.dumps(record), encoding="utf-8")


def test_only_validly_signed_folders_count(tmp_path):
    priv, pub = _key()
    other, _ = _key()
    _install(tmp_path, "account", priv)
    _install(tmp_path, "tampered", priv, tamper=True)
    _install(tmp_path, "forged", other)
    _install(tmp_path, "renamed", priv, record_slug="account")
    (tmp_path / "unsigned").mkdir()
    (tmp_path / "unsigned" / "plugin.json").write_text("{}")
    assert signing.store_signed_slugs(tmp_path, pub) == {"account"}


def test_missing_root_or_not_the_store_key_finds_nothing(tmp_path):
    priv, pub = _key()
    _install(tmp_path, "account", priv)
    assert signing.store_signed_slugs(tmp_path / "nope", pub) == set()
    # Default key = this build's Store key (or none in a dev run): a self-made key never passes.
    assert signing.store_signed_slugs(tmp_path) == set()
