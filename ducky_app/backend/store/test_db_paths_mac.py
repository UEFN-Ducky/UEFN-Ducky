"""installed_app_dir on macOS: the same folder app_paths uses, never ~/AppData."""

from __future__ import annotations

from pathlib import Path

from backend.store import db
from frontend import app_paths


def test_mac_installed_app_dir_is_under_library_application_support(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(db.sys, "platform", "darwin")
    want = tmp_path / "Library" / "Application Support" / "UEFN-Ducky"
    assert db.installed_app_dir() == want
    # The real (unredirected) app data folder app_paths gives on a Mac.
    monkeypatch.delenv("DUCKY_TESTS_ISOLATED_APPDATA", raising=False)
    monkeypatch.setattr(app_paths.sys, "platform", "darwin")
    assert Path(app_paths.local_app_data_base()) / app_paths.APP_DATA_DIR_NAME == want
