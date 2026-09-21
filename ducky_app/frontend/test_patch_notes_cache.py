"""Patch notes cache: network only when the stored version changes."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.store import db
from frontend import duckyos_account as acc
from frontend import version_check


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    db.reset_for_tests()
    try:
        yield
    finally:
        db.reset_for_tests()


def test_app_patch_notes_second_call_is_cached(isolated_cache, monkeypatch) -> None:
    calls = {"n": 0}

    def fake_fetch(*, timeout: float = 8.0):
        calls["n"] += 1
        return {
            "currentVersion": "1.2.184",
            "versions": [{"version": "1.2.184", "changelog": "notes", "created_at": "1"}],
        }, None

    monkeypatch.setattr(version_check, "fetch_remote_payload", fake_fetch)
    monkeypatch.setattr(version_check, "__version__", "1.2.184")
    first = version_check.get_app_patch_notes()
    second = version_check.get_app_patch_notes()
    assert calls["n"] == 1
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["versions"][0]["changelog"] == "notes"


def test_store_item_versions_refetch_only_on_version_change(isolated_cache, monkeypatch) -> None:
    calls = {"n": 0}

    def fake_collect(action, body, allow_anonymous=True, timeout=20.0):
        calls["n"] += 1
        ver = "1.0.0" if calls["n"] == 1 else "1.0.1"
        return {"versions": [{"version": ver, "changelog": f"c{ver}", "created_at": None}]}

    monkeypatch.setattr(acc, "_store_collect", fake_collect)
    a = acc.store_item_versions("demo-notes", "1.0.0")
    b = acc.store_item_versions("demo-notes", "1.0.0")
    c = acc.store_item_versions("demo-notes", "1.0.1")
    assert calls["n"] == 2
    assert a["ok"] is True and "cached" not in a
    assert b.get("cached") is True
    assert c["versions"][0]["version"] == "1.0.1"


def test_fetch_failure_returns_cached_rows(isolated_cache, monkeypatch) -> None:
    state = {"fail": False}

    def fake_fetch(*, timeout: float = 8.0):
        if state["fail"]:
            return None, "urlopen error"
        return {
            "currentVersion": "1.2.184",
            "versions": [{"version": "1.2.184", "changelog": "kept", "created_at": None}],
        }, None

    monkeypatch.setattr(version_check, "fetch_remote_payload", fake_fetch)
    monkeypatch.setattr(version_check, "__version__", "1.2.184")
    version_check.get_app_patch_notes()
    monkeypatch.setattr(version_check, "__version__", "1.2.185")
    state["fail"] = True
    out = version_check.get_app_patch_notes()
    assert out["cached"] is True
    assert out["versions"][0]["changelog"] == "kept"
    assert out["error"] == "urlopen error"
