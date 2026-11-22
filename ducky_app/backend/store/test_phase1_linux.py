"""Phase 1 secrets import on Linux: a legacy credentials.dat sealed with the Linux seal
moves into ducky.db rows and the keys are still there after a restart."""

from __future__ import annotations

import sys
import types

import pytest

from backend.agent import secrets as sec
from backend.store import db
from backend.store.importers import phase1
from backend.store.repos import secrets as secrets_repo


@pytest.fixture(autouse=True)
def _linux(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("DUCKY_STORE_BACKEND", raising=False)
    monkeypatch.setattr(sec, "_is_linux", lambda: True)
    monkeypatch.setattr(sec, "master_key_path", lambda: tmp_path / "master.key")
    monkeypatch.setattr(sec, "_master_key", None)
    monkeypatch.setitem(sys.modules, "keyring", types.ModuleType("keyring"))  # key file fallback
    db.reset_for_tests()
    phase1.reset_for_tests()
    sec.clear_memory_cache()
    yield tmp_path
    sec.clear_memory_cache()
    db.reset_for_tests()
    phase1.reset_for_tests()


def test_legacy_credentials_import_and_survive_restart(tmp_path, monkeypatch) -> None:
    root = db.app_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "credentials.dat").write_bytes(sec._serialize_keys({"openai": "sk-linux"}))

    report = phase1.ensure_report("secrets")
    assert report and report["names"] == ["openai"]
    assert not (root / "credentials.dat").exists()
    assert (root / phase1.LEGACY_DIR / "secrets" / "credentials.dat").is_file()
    blob = secrets_repo.all_blobs()["openai"]
    assert blob[:5] == b"UMCP\x01" and b"sk-linux" not in blob

    # Restart: no cached keys, no master key in memory.
    sec.clear_memory_cache()
    monkeypatch.setattr(sec, "_master_key", None)
    assert sec.get_key("openai") == "sk-linux"
